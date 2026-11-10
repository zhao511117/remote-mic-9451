// Only the pinned HID-over-GATT callback for the selected adapter is touched.
const config = __CONFIG__;
function decide(selected, bytes, active, previous) {
  if (!selected || bytes.length !== 2 || bytes[1] !== 0 || bytes[0] > 13)
    return {suppress:false, trigger:0, previous};
  const key = bytes[0];
  const mapped = [6,7,10,11,12,13].includes(key);
  return {suppress:active && mapped,
    trigger:active && mapped && key !== previous ? key : 0, previous:key};
}
if (typeof module !== 'undefined') module.exports = {decide};
else {
  const driver = Process.getModuleByName('microsoft.bluetooth.profiles.hidovergatt.dll');
  if (Process.arch !== 'x64' || driver.path.toLowerCase().replace(/\\/g,'/') !== config.path)
    throw new Error('driver_identity_mismatch');
  const callback = driver.base.add(config.rva);
  const prefix = new Uint8Array(callback.readByteArray(config.prefix.length));
  if (!prefix.every((v,i)=>v===config.prefix[i])) throw new Error('callback_bytes_mismatch');
  const iid = Memory.alloc(16);
  iid.writeByteArray([0xef,0x0f,0x5a,0x90,0x53,0xbc,0xdf,0x11,0x8c,0x49,0x00,0x1e,0x4f,0xc6,0x86,0xda]);
  let deadline=0, previous=0, sequence=0;
  function method(object,slot,result,args) {
    return new NativeFunction(object.readPointer().add(slot*Process.pointerSize).readPointer(),result,args);
  }
  rpc.exports = {
    renew() { deadline=Date.now()+2000; return true; },
    disable() { deadline=0; previous=0; return true; }
  };
  Interceptor.attach(callback,{onEnter(args) {
    let access=ptr(0);
    try {
      const address=new Uint8Array(args[0].add(config.address_offset).readByteArray(6));
      if (!address.every((v,i)=>v===config.address[i])) return;
      const buffer=args[2], sizeOut=Memory.alloc(4);
      if (buffer.isNull() || method(buffer,7,'int',['pointer','pointer'])(buffer,sizeOut)!==0) return;
      const size=sizeOut.readU32();
      if (size!==2) return;
      const accessOut=Memory.alloc(Process.pointerSize);
      if (method(buffer,0,'int',['pointer','pointer','pointer'])(buffer,iid,accessOut)!==0) return;
      access=accessOut.readPointer();
      const dataOut=Memory.alloc(Process.pointerSize);
      if (method(access,3,'int',['pointer','pointer'])(access,dataOut)!==0) return;
      const data=dataOut.readPointer();
      const bytes=Array.from(new Uint8Array(data.readByteArray(2)));
      const decision=decide(true,bytes,Date.now()<deadline,previous);
      previous=decision.previous;
      if (decision.suppress) {
        // Neutralize before Windows translates this selected Consumer report.
        data.writeByteArray([0,0]);
      }
      if (decision.trigger) send({kind:'key',selector:decision.trigger,sequence:++sequence});
      send({kind:'report',selector:bytes[0],suppressed:decision.suppress});
    } catch (_) {
      deadline=0; send({kind:'fault'});
    } finally {
      if (!access.isNull()) method(access,2,'uint32',['pointer'])(access);
    }
  }});
  send({kind:'ready'});
}
