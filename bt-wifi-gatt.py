#!/usr/bin/python3
import difflib, json, logging, os, re, statistics, subprocess, tempfile, time, unicodedata
from pathlib import Path

import dbus, dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

BLUEZ="org.bluez";ADAPTER="/org/bluez/hci0"
GATT_MANAGER="org.bluez.GattManager1";ADV_MANAGER="org.bluez.LEAdvertisingManager1"
SERVICE_IFACE="org.bluez.GattService1";CHAR_IFACE="org.bluez.GattCharacteristic1"
ADV_IFACE="org.bluez.LEAdvertisement1";PROP_IFACE="org.freedesktop.DBus.Properties";OBJ_MANAGER="org.freedesktop.DBus.ObjectManager"
AGENT_IFACE="org.bluez.Agent1";AGENT_MANAGER="org.bluez.AgentManager1";DEVICE_IFACE="org.bluez.Device1"
SERVICE_UUID="7d7a0000-3e6b-4f4b-9a5a-8f9c4a1e0000"
RX_UUID="7d7a0001-3e6b-4f4b-9a5a-8f9c4a1e0000";TX_UUID="7d7a0002-3e6b-4f4b-9a5a-8f9c4a1e0000"
SETTINGS=Path("/var/lib/meeting-recorder/settings.json")
SOURCE="alsa_input.platform-inmp441-sound.stereo-fallback"
LEVEL_FILE=Path("/run/ai-recorder-audio-level")
logging.basicConfig(level=logging.INFO,format="%(message)s");log=logging.getLogger("bt-wifi-gatt")

def normalize(value):return "".join(c for c in unicodedata.normalize("NFKC",value).casefold() if c.isalnum())
def defaults():return {"gain":4.0,"speech_db":-38.0,"silence_db":-42.0,"silence_delay":1.2,"noise_db":-46.0}
def load_settings():
 data=defaults()
 try:data.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
 except (OSError,ValueError):pass
 return data
def save_settings(data):
 SETTINGS.parent.mkdir(parents=True,exist_ok=True);fd,name=tempfile.mkstemp(dir=SETTINGS.parent,prefix="settings-",text=True)
 try:
  with os.fdopen(fd,"w",encoding="utf-8") as f:json.dump(data,f,ensure_ascii=False);f.write("\n")
  os.chmod(name,0o644);os.replace(name,SETTINGS)
 finally:
  if os.path.exists(name):os.unlink(name)

class Service(dbus.service.Object):
 def __init__(self,bus):self.path="/org/meetingassistant/service0";self.chars=[];super().__init__(bus,self.path)
 def props(self):return {SERVICE_IFACE:{"UUID":SERVICE_UUID,"Primary":dbus.Boolean(True),"Includes":dbus.Array([],signature="o")}}
 @dbus.service.method(PROP_IFACE,in_signature="s",out_signature="a{sv}")
 def GetAll(self,interface):return self.props()[SERVICE_IFACE] if interface==SERVICE_IFACE else {}

class Characteristic(dbus.service.Object):
 def __init__(self,bus,index,uuid,flags,service):
  self.path=f"{service.path}/char{index}";self.uuid=uuid;self.flags=flags;self.service=service;service.chars.append(self);super().__init__(bus,self.path)
 def props(self):return {CHAR_IFACE:{"Service":dbus.ObjectPath(self.service.path),"UUID":self.uuid,"Flags":dbus.Array(self.flags,signature="s"),"Descriptors":dbus.Array([],signature="o")}}
 @dbus.service.method(PROP_IFACE,in_signature="s",out_signature="a{sv}")
 def GetAll(self,interface):return self.props()[CHAR_IFACE] if interface==CHAR_IFACE else {}
 @dbus.service.signal(PROP_IFACE,signature="sa{sv}as")
 def PropertiesChanged(self,interface,changed,invalidated):pass

class Tx(Characteristic):
 def __init__(self,bus,service):super().__init__(bus,1,TX_UUID,["read","notify"],service);self.value=b"READY";self.notifying=False
 def props(self):
  p=super().props();p[CHAR_IFACE]["Notifying"]=dbus.Boolean(self.notifying);p[CHAR_IFACE]["Value"]=dbus.Array(self.value,signature="y");return p
 @dbus.service.method(CHAR_IFACE,in_signature="a{sv}",out_signature="ay")
 def ReadValue(self,options):return dbus.Array(self.value,signature="y")
 @dbus.service.method(CHAR_IFACE)
 def StartNotify(self):self.notifying=True
 @dbus.service.method(CHAR_IFACE)
 def StopNotify(self):self.notifying=False
 def send(self,text):
  self.value=text.encode("utf-8")[:240];log.info("BT_RESPONSE type=%s",text.split("=",1)[0].split(" ",1)[0])
  if self.notifying:self.PropertiesChanged(CHAR_IFACE,{"Value":dbus.Array(self.value,signature="y")},[])

class Handler:
 def __init__(self,tx):self.tx=tx;self.ssid=None;self.password=None;self.open_network=False;self.suggestion=None
 def current_ssid(self):
  p=subprocess.run(["nmcli","-t","--escape","no","-f","ACTIVE,SSID","device","wifi"],capture_output=True,text=True,timeout=10)
  for line in p.stdout.splitlines():
   if line.startswith("yes:"):return line[4:]
  return ""
 def saved_profile(self,ssid):
  p=subprocess.run(["nmcli","-t","--escape","no","-f","NAME,TYPE","connection","show"],capture_output=True,text=True,timeout=10)
  for line in p.stdout.splitlines():
   try:name,kind=line.rsplit(":",1)
   except ValueError:continue
   if kind not in ("802-11-wireless","wifi"):continue
   q=subprocess.run(["nmcli","-g","802-11-wireless.ssid","connection","show",name],capture_output=True,text=True,timeout=10)
   if q.stdout.strip()==ssid:return name
  return None
 def networks(self):
  p=subprocess.run(["nmcli","-t","--escape","no","-f","SSID,SIGNAL","device","wifi","list","ifname","wlan0","--rescan","yes"],capture_output=True,text=True,timeout=30)
  result={}
  for line in p.stdout.splitlines():
   try:ssid,signal=line.rsplit(":",1);strength=int(signal)
   except ValueError:continue
   if ssid:result[ssid]=max(strength,result.get(ssid,0))
  return result
 def choose(self,query):
  q=normalize(query);items=self.networks()
  if not q or not items:raise RuntimeError("СЕТИ НЕ НАЙДЕНЫ")
  def rank(item):
   name,signal=item;n=normalize(name);score=difflib.SequenceMatcher(None,q,n).ratio()
   if q in n or n in q:score=max(score,.75+.25*min(len(q),len(n))/max(len(q),len(n)))
   return score,signal
  (name,_),score=max(items.items(),key=rank),0
  score=rank((name,items[name]))[0];self.ssid=name;return name,round(score*100)
 def measure(self):
  levels=[];deadline=time.monotonic()+10
  while time.monotonic()<deadline:
   try:
    timestamp,value=LEVEL_FILE.read_text(encoding="ascii").split()
    if time.time()-float(timestamp)<1:levels.append(float(value))
   except (OSError,ValueError):pass
   time.sleep(.1)
  if not levels:raise RuntimeError("ЗАМЕР НЕ УДАЛСЯ")
  noise=statistics.median(levels);self.suggestion={"noise_db":round(noise,1),"speech_db":round(noise+8,1),"silence_db":round(noise+4,1)};return self.suggestion
 def connect(self):
  if not self.ssid:raise RuntimeError("НУЖЕН SSID")
  if self.open_network:
   p=subprocess.run(["nmcli","--wait","40","device","wifi","connect",self.ssid,"ifname","wlan0"],capture_output=True,text=True,timeout=50)
  elif self.password is not None:
   p=subprocess.run(["nmcli","--ask","--wait","40","device","wifi","connect",self.ssid,"ifname","wlan0"],input=self.password+"\n",capture_output=True,text=True,timeout=50)
  else:
   profile=self.saved_profile(self.ssid)
   if not profile:raise RuntimeError("ПАРОЛЬ НЕ СОХРАНЕН — ВВЕДИТЕ ПАРОЛЬ")
   p=subprocess.run(["nmcli","--wait","40","connection","up",profile,"ifname","wlan0"],capture_output=True,text=True,timeout=50)
  self.password=None
  if p.returncode:raise RuntimeError("НЕ УДАЛОСЬ ПОДКЛЮЧИТЬСЯ")
  return self.ssid
 def command(self,text):
  cmd=text.strip();upper=cmd.upper();log.info("BT_COMMAND type=%s","PASS" if upper.startswith("PASS=") else upper.split("=",1)[0][:20])
  try:
   if upper=="HELP":self.tx.send("SCAN | SSID=имя | PASS=пароль | CONNECT | GAIN=4 | MEASURE | APPLY | THRESHOLD=-42 | STATUS")
   elif upper=="SCAN":
    nets=self.networks();self.tx.send("СЕТИ="+";".join(sorted(nets,key=nets.get,reverse=True)[:8]))
   elif upper.startswith("SSID="):
    name,score=self.choose(cmd.split("=",1)[1]);self.tx.send(f"ВЫБРАНО={name};СХОДСТВО={score}%")
   elif upper.startswith("PASS="):self.password=cmd.split("=",1)[1];self.tx.send("ПАРОЛЬ=ПРИНЯТ")
   elif upper.startswith("OPEN="):self.open_network=cmd.split("=",1)[1] in ("1","TRUE","YES");self.tx.send("ОТКРЫТАЯ="+("ДА" if self.open_network else "НЕТ"))
   elif upper=="CONNECT":self.tx.send("ПОДКЛЮЧЕНИЕ");name=self.connect();self.tx.send("ПОДКЛЮЧЕНО="+name)
   elif upper=="MEASURE":
    self.tx.send("ЗАМЕР=10С");s=self.measure();self.tx.send(f"ШУМ={s['noise_db']}DB;РЕЧЬ={s['speech_db']}DB;ТИШИНА={s['silence_db']}DB")
   elif upper=="APPLY":
    if not self.suggestion:raise RuntimeError("СНАЧАЛА MEASURE")
    data=load_settings();data.update(self.suggestion);save_settings(data);self.tx.send("ПОРОГИ=СОХРАНЕНЫ")
   elif upper.startswith("GAIN="):
    gain=float(cmd.split("=",1)[1])
    if not 1.0<=gain<=8.0:raise RuntimeError("УСИЛЕНИЕ ДОЛЖНО БЫТЬ ОТ 1 ДО 8")
    data=load_settings();data["gain"]=gain;save_settings(data);self.tx.send(f"УСИЛЕНИЕ={gain:g}")
   elif upper.startswith("THRESHOLD="):
    silence=float(cmd.split("=",1)[1]);data=load_settings();data.update(silence_db=silence,speech_db=silence+4);save_settings(data);self.tx.send(f"ТИШИНА={silence}DB;РЕЧЬ={silence+4}DB")
   elif upper=="STATUS":
    d=load_settings();self.ssid=self.ssid or self.current_ssid();saved=bool(self.ssid and self.saved_profile(self.ssid));self.tx.send(f"SSID={self.ssid or '-'};ПАРОЛЬ={'СОХРАНЕН' if saved else 'НЕТ'};УСИЛЕНИЕ={d['gain']};ШУМ={d['noise_db']}DB;РЕЧЬ={d['speech_db']}DB;ТИШИНА={d['silence_db']}DB")
   else:self.tx.send("ОШИБКА=НЕИЗВЕСТНАЯ КОМАНДА")
  except Exception as e:self.password=None if upper=="CONNECT" else self.password;self.tx.send("ОШИБКА="+str(e)[:160]);log.warning("BT_COMMAND_FAILED type=%s",upper.split("=",1)[0])

class Rx(Characteristic):
 def __init__(self,bus,service,handler):super().__init__(bus,0,RX_UUID,["write","write-without-response"],service);self.handler=handler
 @dbus.service.method(CHAR_IFACE,in_signature="aya{sv}")
 def WriteValue(self,value,options):self.handler.command(bytes(value).decode("utf-8","replace"))

class Application(dbus.service.Object):
 def __init__(self,bus):
  self.path="/";super().__init__(bus,self.path);self.service=Service(bus);self.tx=Tx(bus,self.service);self.handler=Handler(self.tx);self.rx=Rx(bus,self.service,self.handler)
 @dbus.service.method(OBJ_MANAGER,out_signature="a{oa{sa{sv}}}")
 def GetManagedObjects(self):
  result={self.service.path:self.service.props()}
  for c in self.service.chars:result[c.path]=c.props()
  return result

class Advertisement(dbus.service.Object):
 def __init__(self,bus):self.path="/org/meetingassistant/advertisement0";super().__init__(bus,self.path)
 @dbus.service.method(PROP_IFACE,in_signature="s",out_signature="a{sv}")
 def GetAll(self,interface):
  if interface!=ADV_IFACE:return {}
  return {"Type":"peripheral","LocalName":"Meeting Assistant","ServiceUUIDs":dbus.Array([SERVICE_UUID],signature="s"),"Discoverable":dbus.Boolean(True)}
 @dbus.service.method(ADV_IFACE)
 def Release(self):log.info("BT_ADVERTISEMENT_RELEASED")

class Agent(dbus.service.Object):
 def __init__(self,bus):self.path="/org/meetingassistant/agent";super().__init__(bus,self.path)
 @dbus.service.method(AGENT_IFACE)
 def Release(self):log.info("BT_AGENT_RELEASED")
 @dbus.service.method(AGENT_IFACE,in_signature="o",out_signature="s")
 def RequestPinCode(self,device):return "000000"
 @dbus.service.method(AGENT_IFACE,in_signature="o",out_signature="u")
 def RequestPasskey(self,device):return dbus.UInt32(0)
 @dbus.service.method(AGENT_IFACE,in_signature="ouq")
 def DisplayPasskey(self,device,passkey,entered):pass
 @dbus.service.method(AGENT_IFACE,in_signature="os")
 def DisplayPinCode(self,device,pincode):pass
 @dbus.service.method(AGENT_IFACE,in_signature="ou")
 def RequestConfirmation(self,device,passkey):log.info("BT_PAIRING_CONFIRMED device=%s",device)
 @dbus.service.method(AGENT_IFACE,in_signature="o")
 def RequestAuthorization(self,device):log.info("BT_DEVICE_AUTHORIZED device=%s",device)
 @dbus.service.method(AGENT_IFACE,in_signature="os")
 def AuthorizeService(self,device,uuid):log.info("BT_SERVICE_AUTHORIZED uuid=%s",uuid)
 @dbus.service.method(AGENT_IFACE)
 def Cancel(self):log.info("BT_AGENT_CANCEL")

def main():
 DBusGMainLoop(set_as_default=True);bus=dbus.SystemBus();app=Application(bus);adv=Advertisement(bus);agent=Agent(bus);loop=GLib.MainLoop()
 adapter=bus.get_object(BLUEZ,ADAPTER)
 agent_manager=dbus.Interface(bus.get_object(BLUEZ,"/org/bluez"),AGENT_MANAGER)
 agent_manager.RegisterAgent(agent.path,"DisplayYesNo");agent_manager.RequestDefaultAgent(agent.path);log.info("BT_AGENT_READY")
 def device_changed(interface,changed,invalidated,path=None):
  if interface!=DEVICE_IFACE or "Connected" not in changed:return
  if bool(changed["Connected"]):Path("/run/bt-client-connected").touch();log.info("BT_CLIENT_CONNECTED device=%s",path)
  else:
   Path("/run/bt-client-connected").unlink(missing_ok=True);log.info("BT_CLIENT_DISCONNECTED device=%s",path)
 bus.add_signal_receiver(device_changed,dbus_interface=PROP_IFACE,signal_name="PropertiesChanged",path_keyword="path")
 object_manager=dbus.Interface(bus.get_object(BLUEZ,"/"),OBJ_MANAGER)
 last_connected=[False]
 def poll_connections():
  try:
   objects=object_manager.GetManagedObjects()
   connected=any(bool(props.get("Connected",False)) for interfaces in objects.values() for name,props in interfaces.items() if name==DEVICE_IFACE)
   marker=Path("/run/bt-client-connected")
   if connected:marker.touch()
   else:marker.unlink(missing_ok=True)
   if connected!=last_connected[0]:log.info("BT_CLIENT_%s","CONNECTED" if connected else "DISCONNECTED");last_connected[0]=connected
  except Exception as e:log.warning("BT_CONNECTION_POLL_FAILED %s",e)
  return True
 GLib.timeout_add(500,poll_connections)
 dbus.Interface(adapter,GATT_MANAGER).RegisterApplication(app.path,{},reply_handler=lambda:log.info("BT_GATT_READY name=Meeting Assistant"),error_handler=lambda e:(log.error("GATT_ERROR %s",e),loop.quit()))
 dbus.Interface(adapter,ADV_MANAGER).RegisterAdvertisement(adv.path,{},reply_handler=lambda:log.info("BT_ADVERTISEMENT_READY"),error_handler=lambda e:(log.error("ADV_ERROR %s",e),loop.quit()))
 try:loop.run()
 finally:Path("/run/bt-client-connected").unlink(missing_ok=True)
if __name__=="__main__":main()
