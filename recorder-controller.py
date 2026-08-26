#!/usr/bin/python3
import logging, os, signal, subprocess, time
import gpiod
BUTTON_CHIP="gpiochip4";BUTTON_LINE=17;DEBOUNCE_SECONDS=.35;RADXA_UID=1000
USER_SERVICE="audio-recorder.service";UPLOAD_SERVICE="meeting-upload.service";STATE_FILE="/run/ai-recorder-state"
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
 log.info("RECORDING_START_REQUESTED");state("STARTING");result=user_systemctl("start",USER_SERVICE)
 if result.returncode:log.error("RECORDING_START_FAILED %s",result.stderr.strip());state("ERROR");return False
 time.sleep(.5);active=recording_active();log.info("RECORDING_STARTED" if active else "RECORDING_START_FAILED inactive");state("RECORDING" if active else "ERROR");return active
def stop_recording(reason):
 log.info("RECORDING_STOP_REQUESTED reason=%s",reason);state("STOPPING");result=user_systemctl("stop",USER_SERVICE)
 if result.returncode:log.error("RECORDING_STOP_FAILED %s",result.stderr.strip());state("ERROR");return False
 log.info("RECORDING_STOPPED");upload=user_systemctl("start","--no-block",UPLOAD_SERVICE)
 if upload.returncode:log.error("MEETING_UPLOAD_QUEUE_FAILED %s",upload.stderr.strip());state("ERROR")
 else:log.info("MEETING_UPLOAD_QUEUED");state("PROCESSING")
 return True
def main():
 chip=gpiod.Chip(BUTTON_CHIP);line=chip.get_line(BUTTON_LINE);line.request(consumer="ai-recorder-button",type=gpiod.LINE_REQ_EV_BOTH_EDGES)
 running=True
 def terminate(*_):
  nonlocal running;running=False
 signal.signal(signal.SIGTERM,terminate);signal.signal(signal.SIGINT,terminate)
 last_event_at=0.;ignore_until=0.;state("RECORDING" if recording_active() else "READY")
 log.info("CONTROLLER_READY button=gpiochip4:17 falling-edge=toggle debounce=350ms shutdown=disabled")
 try:
  while running:
   now=time.monotonic()
   if not line.event_wait(sec=0,nsec=50_000_000):continue
   event=line.event_read();now=time.monotonic()
   if now<ignore_until:continue
   if now-last_event_at<DEBOUNCE_SECONDS:continue
   last_event_at=now
   if event.type==gpiod.LineEvent.FALLING_EDGE:
    active=recording_active();log.info("BUTTON_PRESSED recording=%s",str(active).lower())
    if active:
     log.info("BUTTON_PRESSED_DURING_RECORDING");stop_recording("button")
    else:start_recording()
    while line.event_wait(sec=0,nsec=0):line.event_read()
    last_event_at=time.monotonic();ignore_until=last_event_at+1.0
    continue
   if event.type==gpiod.LineEvent.RISING_EDGE:log.info("BUTTON_RELEASED")
 finally:line.release();chip.close()
if __name__=="__main__":main()
