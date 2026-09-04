#!/usr/bin/python3
import logging, os, signal, subprocess, time
import gpiod
BUTTON_CHIP="gpiochip4";BUTTON_LINE=17;DEBOUNCE_SECONDS=.08;LONG_PRESS_SECONDS=1.5;RADXA_UID=1000
USER_SERVICE="audio-recorder.service";UPLOAD_SERVICE="meeting-upload.service";STATE_FILE="/run/ai-recorder-state"
DISPLAY_STATE_FILE=f"/run/user/{RADXA_UID}/meeting-display.json"
logging.basicConfig(level=logging.INFO,format="%(message)s");log=logging.getLogger("recorder-controller")
def state(value):
 try:
  with open(STATE_FILE,"w",encoding="ascii") as f:f.write(value+"\n")
 except OSError as e:log.warning("STATE_WRITE_FAILED %s",e)
def user_systemctl(*args):
 env=os.environ.copy();env["XDG_RUNTIME_DIR"]=f"/run/user/{RADXA_UID}";env["DBUS_SESSION_BUS_ADDRESS"]=f"unix:path=/run/user/{RADXA_UID}/bus"
 return subprocess.run(["runuser","-u","radxa","--","systemctl","--user",*args],env=env,check=False,capture_output=True,text=True)
def recording_active():return user_systemctl("is-active","--quiet",USER_SERVICE).returncode==0
def bt_configuration_active():return subprocess.run(["systemctl","is-active","--quiet","bt-pairing-mode.service"],check=False).returncode==0
def start_recording():
 if bt_configuration_active():
  log.warning("RECORDING_START_BLOCKED reason=bt-configuration");return False
 log.info("RECORDING_START_REQUESTED");state("STARTING")
 # PipeWire can keep a valid-looking I2S source that only returns digital
 # silence after boot.  Reopening the ALSA device through a fresh graph
 # reliably restores it, and doing this before recording avoids touching a
 # running meeting.
 audio_reset=user_systemctl("restart","wireplumber.service","pipewire.service","pipewire-pulse.service")
 if audio_reset.returncode:log.warning("AUDIO_SERVER_RESET_FAILED %s",audio_reset.stderr.strip())
 else:log.info("AUDIO_SERVER_RESET_OK")
 time.sleep(2)
 result=user_systemctl("start",USER_SERVICE)
 if result.returncode:log.error("RECORDING_START_FAILED %s",result.stderr.strip());state("ERROR");return False
 time.sleep(.5);active=recording_active();log.info("RECORDING_STARTED" if active else "RECORDING_START_FAILED inactive");state("RECORDING" if active else "ERROR");return active
def queue_upload():
 upload=user_systemctl("start","--no-block",UPLOAD_SERVICE)
 if upload.returncode:log.error("MEETING_UPLOAD_QUEUE_FAILED %s",upload.stderr.strip());state("ERROR");return False
 log.info("MEETING_UPLOAD_QUEUED");state("PROCESSING");return True
def stop_recording(reason):
 log.info("RECORDING_STOP_REQUESTED reason=%s",reason);state("STOPPING");result=user_systemctl("stop",USER_SERVICE)
 if result.returncode:log.error("RECORDING_STOP_FAILED %s",result.stderr.strip());state("ERROR");return False
 log.info("RECORDING_STOPPED");return queue_upload()
def clear_meeting_qr():
 try:os.unlink(DISPLAY_STATE_FILE);log.info("MEETING_QR_CLEARED");return True
 except FileNotFoundError:return False
 except OSError as e:log.warning("MEETING_QR_CLEAR_FAILED %s",e);return False
def main():
 chip=gpiod.Chip(BUTTON_CHIP);line=chip.get_line(BUTTON_LINE);line.request(consumer="ai-recorder-button",type=gpiod.LINE_REQ_EV_BOTH_EDGES)
 running=True
 def terminate(*_):
  nonlocal running;running=False
 signal.signal(signal.SIGTERM,terminate);signal.signal(signal.SIGINT,terminate)
 last_event_at=0.;press_started=None;press_was_recording=False;long_action_fired=False;last_status_check=0.;was_recording=recording_active();state("RECORDING" if was_recording else "READY")
 log.info("CONTROLLER_READY button=gpiochip4:17 short=start-or-clear-qr long=stop-at-threshold threshold=1.5s")
 try:
  while running:
   event_ready=line.event_wait(sec=0,nsec=50_000_000);now=time.monotonic()
   if not event_ready:
    if press_started is not None and press_was_recording and not long_action_fired and now-press_started>=LONG_PRESS_SECONDS:
     log.info("BUTTON_LONG_PRESS_THRESHOLD held=%.2fs",now-press_started)
     stop_recording("long-button-hold");was_recording=False;long_action_fired=True
    if now-last_status_check>=1.0:
     last_status_check=now;active=recording_active()
     if was_recording and not active:
      log.info("RECORDING_AUTO_STOPPED limit=6h");queue_upload()
     was_recording=active
    continue
   event=line.event_read();now=time.monotonic()
   if now-last_event_at<DEBOUNCE_SECONDS:continue
   last_event_at=now
   if event.type==gpiod.LineEvent.FALLING_EDGE:
    active=recording_active();log.info("BUTTON_PRESSED recording=%s",str(active).lower())
    if not active and clear_meeting_qr():
     state("READY");press_started=None;press_was_recording=False;long_action_fired=False
     continue
    press_started=now;press_was_recording=active;long_action_fired=False
    continue
   if event.type==gpiod.LineEvent.RISING_EDGE:
    if press_started is None:
     log.info("BUTTON_RELEASED after=qr-clear");continue
    held=now-press_started;started_while_recording=press_was_recording;press_started=None;press_was_recording=False;active=recording_active()
    log.info("BUTTON_RELEASED held=%.2fs recording=%s",held,str(active).lower())
    if long_action_fired:
     long_action_fired=False;continue
    if started_while_recording:
     if held>=LONG_PRESS_SECONDS:
      stop_recording("long-button-hold-release-fallback");was_recording=False
     else:log.info("SHORT_PRESS_IGNORED_DURING_RECORDING")
    elif held<LONG_PRESS_SECONDS:
     was_recording=start_recording()
    else:log.info("LONG_PRESS_IGNORED_WHILE_IDLE")
 finally:line.release();chip.close()
if __name__=="__main__":main()
