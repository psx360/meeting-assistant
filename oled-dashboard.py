#!/usr/bin/python3
import array, fcntl, glob, json, math, os, signal, subprocess, threading, time
import gpiod
I2C_DEV="/dev/i2c-0"; I2C_ADDR=0x3C; STATE_FILE="/run/ai-recorder-state"; LEVEL_FILE="/run/ai-recorder-audio-level"
SOURCE="alsa_input.platform-inmp441-sound.stereo-fallback"
DEFAULT_MIC_GAIN=float(os.environ.get("MIC_GAIN","4"))
SETTINGS_FILE="/var/lib/meeting-recorder/settings.json"
FONT={
" ":[0,0,0,0,0],"-":[8,8,8,8,8],".":[0,0,0,96,96],":":[0,54,54,0,0],"/":[64,32,16,8,4],"!":[0,0,95,0,0],"?":[2,1,81,9,6],
"0":[62,81,73,69,62],"1":[0,66,127,64,0],"2":[66,97,81,73,70],"3":[33,65,69,75,49],"4":[24,20,18,127,16],"5":[39,69,69,69,57],"6":[60,74,73,73,48],"7":[1,113,9,5,3],"8":[54,73,73,73,54],"9":[6,73,73,41,30],
"A":[126,17,17,17,126],"B":[127,73,73,73,54],"C":[62,65,65,65,34],"D":[127,65,65,34,28],"E":[127,73,73,73,65],"F":[127,9,9,9,1],"G":[62,65,73,73,122],"H":[127,8,8,8,127],"I":[0,65,127,65,0],"J":[32,64,65,63,1],"K":[127,8,20,34,65],"L":[127,64,64,64,64],"M":[127,2,12,2,127],"N":[127,4,8,16,127],"O":[62,65,65,65,62],"P":[127,9,9,9,6],"Q":[62,65,81,33,94],"R":[127,9,25,41,70],"S":[38,73,73,73,50],"T":[1,1,127,1,1],"U":[63,64,64,64,63],"V":[31,32,64,32,31],"W":[63,64,56,64,63],"X":[99,20,8,20,99],"Y":[3,4,120,4,3],"Z":[97,81,73,69,67]}
FONT.update({
"А":FONT["A"],"Б":[127,73,73,73,49],"В":FONT["B"],"Г":[127,1,1,1,1],
"Д":[96,62,33,33,127],"Е":FONT["E"],"Ё":[125,84,84,84,69],"Ж":[119,8,127,8,119],
"З":[34,65,73,73,54],"И":[127,32,16,8,127],"Й":[124,33,17,9,124],"К":FONT["K"],
"Л":[64,62,1,1,127],"М":FONT["M"],"Н":FONT["H"],"О":FONT["O"],
"П":[127,1,1,1,127],"Р":FONT["P"],"С":FONT["C"],"Т":FONT["T"],
"У":[7,72,72,72,63],"Ф":[28,34,127,34,28],"Х":FONT["X"],"Ц":[63,32,32,127,96],
"Ч":[15,8,8,8,127],"Ш":[127,64,127,64,127],"Щ":[63,32,63,32,127],
"Ъ":[1,127,72,72,48],"Ы":[127,72,48,0,127],"Ь":[127,72,72,72,48],
"Э":[34,73,73,73,62],"Ю":[127,8,62,65,62],"Я":[70,41,25,9,127]})
FONT[">"]=[0,65,34,20,8]
FONT["*"]=[20,8,62,8,20]
class Display:
 def __init__(self):
  self.fd=os.open(I2C_DEV,os.O_RDWR); fcntl.ioctl(self.fd,0x0703,I2C_ADDR); self.buf=bytearray(1024)
  self.cmd(0xAE,0xD5,0x80,0xA8,0x3F,0xD3,0,0x40,0xAD,0x8B,0xA1,0xC8,0xDA,0x12,0x81,0x45,0xD9,0x22,0xDB,0x35,0xA4,0xA6,0xAF)
 def cmd(self,*v):
  for i in range(0,len(v),16): os.write(self.fd,bytes([0])+bytes(v[i:i+16]))
 def clear(self): self.buf[:]=b'\0'*1024
 def pixel(self,x,y):
  if 0<=x<128 and 0<=y<64: self.buf[(y//8)*128+x]|=1<<(y&7)
 def text(self,x,y,s,scale=1):
  for ch in s.upper():
   for gx,col in enumerate(FONT.get(ch,FONT["?"])):
    for gy in range(7):
     if col&(1<<gy):
      for dx in range(scale):
       for dy in range(scale): self.pixel(x+gx*scale+dx,y+gy*scale+dy)
   x+=6*scale
 def centered(self,y,s,scale=1): self.text(max(0,(128-len(s)*6*scale+scale)//2),y,s,scale)
 def line(self,x,y0,y1):
  for y in range(y0,y1+1): self.pixel(x,y)
 def show(self):
  for p in range(8):
   self.cmd(0xB0+p,2,0x10); row=self.buf[p*128:(p+1)*128]
   for i in range(0,128,16): os.write(self.fd,bytes([0x40])+row[i:i+16])
class Meter:
 def __init__(self): self.db=-90.; self.running=False; self.proc=None; self.lock=threading.Lock();self.gain=DEFAULT_MIC_GAIN
 def start(self,gain):
  self.gain=gain
  if not self.running:self.running=True;threading.Thread(target=self._run,daemon=True).start()
 def stop(self):
  self.running=False
  if self.proc:
   try:self.proc.terminate()
   except ProcessLookupError:pass
  self.proc=None; self.db=-90.
 def _run(self):
  env=os.environ.copy(); env.update(XDG_RUNTIME_DIR="/run/user/1000",DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/1000/bus")
  try:
   self.proc=subprocess.Popen(["runuser","-u","radxa","--","ffmpeg","-nostdin","-hide_banner","-loglevel","error","-f","pulse","-i",SOURCE,"-af",f"volume={self.gain:g}","-ac","1","-ar","8000","-f","s16le","pipe:1"],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env)
   while self.running:
    raw=self.proc.stdout.read(1600)
    if not raw:break
    a=array.array('h',raw); rms=math.sqrt(sum(v*v for v in a)/max(1,len(a))); db=20*math.log10(max(1,rms)/32768.)
    with self.lock:self.db=max(-90.,min(0.,db))
    try:
     with open(LEVEL_FILE,"w",encoding="ascii") as f:f.write(f"{time.time():.3f} {self.db:.2f}\n")
    except OSError:pass
  except Exception:self.db=-90.
  finally:self.running=False
 def value(self):
  with self.lock:return self.db
class Controls:
 def __init__(self):
  self.chip4=gpiod.Chip("gpiochip4");self.chip1=gpiod.Chip("gpiochip1");self.lines=[];self.last={}
  self.encoder_a=self._event(self.chip4,15,"oled-encoder-a")
  self.encoder_b=self.chip4.get_line(16);self.encoder_b.request(consumer="oled-encoder-b",type=gpiod.LINE_REQ_DIR_IN)
  self.knob=self._event(self.chip4,22,"oled-encoder-button")
  self.back=self._event(self.chip4,13,"oled-back")
  self.confirm=self._event(self.chip1,8,"oled-confirm")
 def _event(self,chip,offset,name):
  line=chip.get_line(offset);line.request(consumer=name,type=gpiod.LINE_REQ_EV_FALLING_EDGE);self.lines.append(line);return line
 def poll(self):
  result=[];now=time.monotonic()
  for name,line in (("ROTATE",self.encoder_a),("KNOB",self.knob),("BACK",self.back),("CONFIRM",self.confirm)):
   if line.event_wait(sec=0,nsec=0):
    line.event_read()
    while line.event_wait(sec=0,nsec=0):line.event_read()
    debounce=.22 if name=="ROTATE" else .50
    if now-self.last.get(name,0)>debounce:
     self.last[name]=now
     if name=="ROTATE":result.append("RIGHT" if self.encoder_b.get_value() else "LEFT")
     else:result.append(name)
  return result
 def close(self):
  for line in self.lines:
   try:line.release()
   except Exception:pass
  try:self.encoder_b.release()
  except Exception:pass
  self.chip4.close();self.chip1.close()
def user_active(unit):
 env=os.environ.copy(); env.update(XDG_RUNTIME_DIR="/run/user/1000",DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/1000/bus")
 return subprocess.run(["runuser","-u","radxa","--","systemctl","--user","is-active","--quiet",unit],env=env).returncode==0
def system_active(unit):return subprocess.run(["systemctl","is-active","--quiet",unit]).returncode==0
def requested():
 try:
  with open(STATE_FILE) as f:return f.read().strip().upper()
 except OSError:return ""
def wifi_ok():return "wlan0:connected" in subprocess.run(["nmcli","-t","-f","DEVICE,STATE","device"],capture_output=True,text=True).stdout
def upload_pending():
 for ready in glob.glob("/home/radxa/audio-split-test/*/.ready"):
  if not os.path.exists(os.path.join(os.path.dirname(ready),".uploaded")):return True
 return False
def disk_ok():
 try:s=os.statvfs("/home/radxa");return s.f_bavail*s.f_frsize>1024**3
 except OSError:return False
def audio_settings():
 try:
  with open(SETTINGS_FILE,encoding="utf-8") as f:data=json.load(f)
  return float(data.get("gain",DEFAULT_MIC_GAIN)),float(data.get("speech_db",-38)),float(data.get("silence_db",-42))
 except (OSError,ValueError,TypeError):return DEFAULT_MIC_GAIN,-38.,-42.
def duration(s):return f"{int(s)//3600:02}:{(int(s)//60)%60:02}:{int(s)%60:02}"
def main():
 d=Display();m=Meter();controls=Controls();alive=True;active=upload=pending=wifi=bt_active=False;started=0.;silence=None;speech=False;hist=[0]*20;last=0;inactive_checks=0;menu=False;menu_index=0;shutdown_confirm=False;mic_gain,speech_db,silence_db=audio_settings()
 def stop(*_):
  nonlocal alive;alive=False
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 while alive:
  now=time.monotonic()
  if now-last>=1:
   observed_active=user_active("audio-recorder.service")
   if observed_active:
    inactive_checks=0
    if not active:active=True;started=now
   else:
    inactive_checks+=1
    if inactive_checks>=5:active=False
   upload=user_active("meeting-upload.service");pending=upload_pending();bt_active=system_active("bt-pairing-mode.service");wifi=wifi_ok();new_gain,speech_db,silence_db=audio_settings();last=now
   if new_gain!=mic_gain:
    mic_gain=new_gain
    if m.running:m.stop()
   if (active or bt_active) and not m.running:m.start(mic_gain)
   if not (active or bt_active) and m.running:m.stop()
   if active and not started:started=now
   if not active and inactive_checks>=5:started=0;silence=None;speech=False;hist=[0]*20
  db=m.value()
  if active:
   if db >= speech_db:
    speech=True;silence=None
   elif db < silence_db:
    if silence is None:silence=now
    if now-silence>=1.2:speech=False
   silent=not speech;hist=(hist+[max(0,min(15,int((db+60)/4)))])[-20:]
  else:silent=False
  req=requested()
  # STOPPING/PROCESSING comes directly from the button controller and is
  # authoritative: discard the delayed systemd "active" cache immediately.
  if req in ("STOPPING","PROCESSING"):
   active=False;inactive_checks=5;started=0;silence=None;speech=False;hist=[0]*20
   if m.running:m.stop()
  try:state_age=time.time()-os.path.getmtime(STATE_FILE)
  except OSError:state_age=999
  if req in ("STARTING","STOPPING","ERROR"):state=req
  elif req=="PROCESSING" and (upload or state_age<3):state="PROCESSING"
  elif active:state="SILENCE" if silent else "SPEECH"
  elif upload:state="PROCESSING"
  elif pending:state="PROCESSING" if wifi else "WAIT_NETWORK"
  elif bt_active:state="BT_CONNECTED" if os.path.exists("/run/bt-client-connected") else "BT_PAIRING"
  else:state="READY"
  if state not in ("READY","MENU","SHUTDOWN_CONFIRM"):
   menu=False;shutdown_confirm=False
  for event in controls.poll():
   if event=="KNOB" and state=="READY" and not menu:menu=True;menu_index=0
   elif event in ("LEFT","RIGHT") and menu:
    menu_index=(menu_index+(-1 if event=="LEFT" else 1))%3
   elif event=="BACK" and shutdown_confirm:shutdown_confirm=False;state="READY"
   elif event=="BACK" and menu:menu=False
   elif event=="BACK" and bt_active:
    subprocess.run(["systemctl","stop","bt-pairing-mode.service"],check=False);bt_active=False
    try:os.unlink("/run/bt-client-connected")
    except FileNotFoundError:pass
    state="READY" if not active and not upload else state
   elif event in ("KNOB","CONFIRM") and menu:
    if menu_index==0:
     subprocess.run(["systemctl","restart","bt-pairing-mode.service"],check=False);bt_active=True;menu=False;state="BT_PAIRING"
    elif menu_index==1:
     menu=False;shutdown_confirm=True;state="SHUTDOWN_CONFIRM"
    else:menu=False
   elif event in ("KNOB","CONFIRM") and shutdown_confirm:
    d.clear();d.centered(16,"ВЫКЛЮЧЕНИЕ",2);d.centered(42,"ПОДОЖДИТЕ");d.show()
    subprocess.Popen(["systemctl","poweroff"])
    shutdown_confirm=False
  if shutdown_confirm:state="SHUTDOWN_CONFIRM"
  elif menu:state="MENU"
  d.clear()
  if state!="READY":
   for x in range(3,8):
    for y in range(2,7):
     if (x-5)**2+(y-4)**2<=6:d.pixel(x,y)
  top_names={"STARTING":"ЗАПУСК","STOPPING":"СТОП","PROCESSING":"ОТПР","WAIT_NETWORK":"ОЧЕРЕДЬ","ERROR":"ОШИБКА","BT_PAIRING":"BT","BT_CONNECTED":"BT","MENU":"МЕНЮ","SHUTDOWN_CONFIRM":"ПИТАНИЕ"}
  top=("ЗАП" if active else "ОТПР" if upload else "" if state=="READY" else top_names.get(state,state))
  d.text(12,1,top[:10]);d.text(96,1,time.strftime("%H:%M"))
  if state=="READY":
   d.centered(18,"ГОТОВ",2);d.centered(39,"НАЖМИТЕ КНОПКУ");d.text(1,56,"МИК ОК");d.text(80,56,"СЕТЬ ОК" if wifi else "СЕТЬ НЕТ")
  elif state=="MENU":
   d.centered(8,"МЕНЮ",2);d.text(5,31,(">" if menu_index==0 else " ")+" BT СОПРЯЖЕНИЕ");d.text(5,44,(">" if menu_index==1 else " ")+" ВЫКЛЮЧИТЬ");d.text(5,57,(">" if menu_index==2 else " ")+" НАЗАД")
  elif state=="SHUTDOWN_CONFIRM":
   d.centered(13,"ВЫКЛЮЧИТЬ?",2);d.centered(39,"OK - ДА");d.centered(54,"BACK - НЕТ")
  elif state=="BT_PAIRING":
   d.centered(15,"BT РЕЖИМ",2);d.centered(37,"ПОИСК 15 МИН");d.centered(53,"MEETING ASSISTANT")
  elif state=="BT_CONNECTED":
   d.centered(15,"BT",2);d.centered(34,"ПОДКЛЮЧЕН",2);d.centered(55,"MEETING ASSISTANT")
  elif state in ("STARTING","STOPPING"):
   d.centered(17,"ЗАПУСК" if state=="STARTING" else "ОСТАНОВКА");d.centered(32,"ПОДГОТОВКА МИК" if state=="STARTING" else "СОХРАНЕНИЕ");d.text(7,54,"МИК ОК");d.text(80,54,"СЕТЬ ОК" if wifi else "СЕТЬ НЕТ")
  elif state=="PROCESSING":
   d.centered(17,"ОБРАБОТКА");d.centered(32,"ОТПРАВКА",2);d.centered(55,"СЕТЬ ОК" if wifi else "ЖДЕМ СЕТЬ")
  elif state=="WAIT_NETWORK":
   d.centered(14,"ОЖИДАЕТ",2);d.centered(34,"СЕТЬ",2);d.centered(55,"ЗАПИСЬ В ОЧЕРЕДИ")
  elif state=="ERROR":d.centered(18,"ОШИБКА",2);d.centered(43,"СМОТРИ ЖУРНАЛ")
  else:
   d.centered(15,(f"ТИШИНА {int(now-silence) if silence is not None else 0} С") if silent else "РЕЧЬ")
   for i,h in enumerate(hist):d.line(3+i*6,45-max(1,h),45);d.line(4+i*6,45-max(1,h),45)
   d.text(1,55,duration(now-started));d.text(85,55,"СЕТЬ ОК" if wifi else "СЕТЬ НЕТ")
  d.show();time.sleep(.15)
 m.stop();controls.close();d.clear();d.show()
if __name__=="__main__":main()
