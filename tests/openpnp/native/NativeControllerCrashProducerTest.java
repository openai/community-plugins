/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.*;
import java.lang.reflect.Field;
import java.net.*;
import java.nio.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;
import java.util.regex.*;
import org.openpnp.codex.prototype.TypedGcodeProfile;
import org.openpnp.machine.reference.ReferenceMachine;
import org.openpnp.model.*;

/** Three own-process Runtime.halt seams around the immutable actual Bridge and native diagnostic. */
public final class NativeControllerCrashProducerTest {
    static final Gson JSON=new Gson();
    static String mode;static Path folder;static Bridge bridge;static Peer peer;static ForceObserver channel;
    static JsonObject request,capabilities;static int assertions;
    static void check(boolean value,String message){assertions++;if(!value)throw new AssertionError(message);}
    static JsonObject args(Object...pairs){JsonObject p=new JsonObject();for(int i=0;i<pairs.length;i+=2)p.add((String)pairs[i],JSON.toJsonTree(pairs[i+1]));return p;}
    static JsonObject call(String method,JsonObject p)throws Exception{return NativeControllerRecoveryTest.call(bridge,method,p);}
    static Object field(Object object,String name)throws Exception{Field f=object.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(object);}
    static String sha(byte[] bytes)throws Exception{StringBuilder out=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(bytes))out.append(String.format("%02x",b&255));return out.toString();}
    static void forcedJson(Path path,JsonObject value)throws IOException{byte[] data=(value+"\n").getBytes(StandardCharsets.UTF_8);try(FileChannel out=FileChannel.open(path,StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE)){ByteBuffer b=ByteBuffer.wrap(data);while(b.hasRemaining())out.write(b);out.force(true);}}
    static JsonArray records(byte[] bytes){JsonArray all=new JsonArray();for(String line:new String(bytes,StandardCharsets.UTF_8).split("\n"))if(!line.isBlank())all.add(JSON.fromJson(line,JsonObject.class));return all;}

    static void crash(String boundary,JsonObject forcedEvent)throws Exception{
        // Every caller owns this monitor: no later journal publication can overtake the captured boundary.
        check(Thread.holdsLock(bridge),"Crash evidence retains the actual Bridge publication monitor");
        byte[] journal=Files.readAllBytes(folder.resolve("journal/operations.jsonl"));JsonArray events=records(journal);JsonObject last=events.get(events.size()-1).getAsJsonObject();
        check(last.equals(forcedEvent),"Last on-disk complete record is the observed real forced record");
        check(journal.length>0&&journal[journal.length-1]==10,"Crash journal ends at an entire JSONL record");
        JsonObject admission=null;for(JsonElement raw:events){JsonObject event=raw.getAsJsonObject();if(event.get("type").getAsString().equals("controller_diagnostic_admission"))admission=event.getAsJsonObject("payload");}
        check(admission!=null,"Original typed controller admission is durable");String id=admission.get("operation_id").getAsString();
        Map<?,?> operations=(Map<?,?>)field(bridge,"operations");JsonObject live=JSON.toJsonTree(operations.get(id)).getAsJsonObject();
        check(live.get("state").getAsString().equals("running"),"Live original operation has not committed terminal success");
        check(live.get("request_id").equals(request.get("request_id")),"Crash receipt retains original request");
        check(live.get("controller_instance_id").equals(request.get("controller_instance_id")),"Crash receipt retains original controller identity");
        JsonObject history=JSON.toJsonTree(((NativeControllerJournal)field(bridge,"controllerJournal")).snapshot()).getAsJsonObject();
        JsonObject wire=peer.snapshot();long sequence=((Number)field(bridge,"sequence")).longValue();
        boolean postForce=mode.equals("connect-intent-force")||mode.equals("succeeded-force");
        check(sequence+(postForce?1:0)==last.get("sequence").getAsLong(),"Recorded event lies at the exact before/after in-memory sequence boundary");
        check(Files.isRegularFile(folder.resolve("configuration/machine.xml")),"Actual native configuration save finished before all selected seams");
        ReferenceMachine machine=(ReferenceMachine)Configuration.get().getMachine();check(!machine.isEnabled()&&!machine.isHomed(),"Native shell stays disabled and unhomed");
        if(mode.equals("connect-intent-force")){
            check(last.get("type").getAsString().equals("controller_diagnostic_step_intent")&&last.getAsJsonObject("payload").get("step").getAsString().equals("connect"),"Forced connect intent selected before native connect dispatch");
            check(wire.get("accepted_connections").getAsInt()==0&&wire.get("command_bytes").getAsInt()==0&&wire.get("response_bytes").getAsInt()==0,"No native controller connection or byte before connect intent returns");
            check(!history.has("pending")&&history.getAsJsonArray("steps").size()==1,"Live reducer has only bind outcome; forced connect intent not committed in memory");
        }else if(mode.equals("identify-before-ack")){
            check(last.get("type").getAsString().equals("controller_diagnostic_step_intent")&&last.getAsJsonObject("payload").get("step").getAsString().equals("identify"),"Crash occurs with durable identify intent and no outcome");
            check(history.getAsJsonObject("pending").equals(last.getAsJsonObject("payload")),"Identify intent already committed in the live reducer");
            check(wire.get("accepted_connections").getAsInt()==1&&wire.getAsJsonArray("commands").size()==3&&wire.getAsJsonArray("acks").size()==2,"Actual M115 observed after exactly two startup ACKs");
            check(wire.getAsJsonArray("commands").get(2).getAsString().startsWith("M115 ;codex:"),"Third actual native command is fixed M115");
        }else{
            check(last.get("type").getAsString().equals("operation")&&last.getAsJsonObject("payload").get("state").getAsString().equals("succeeded"),"Actual succeeded operation force selected");
            JsonObject success=last.getAsJsonObject("payload");check(success.getAsJsonObject("native_completion").get("native_wrapper_completed").getAsBoolean()&&success.getAsJsonObject("native_completion").get("native_wrapper_succeeded").getAsBoolean(),"Forced terminal receipt follows the real successful native Future");
            check(history.getAsJsonArray("steps").size()==4&&history.get("retired").getAsBoolean()&&!history.has("pending"),"All four native outcomes and retirement are already committed");
            check(wire.get("accepted_connections").getAsInt()==1&&wire.getAsJsonArray("commands").size()==3&&wire.getAsJsonArray("acks").size()==3,"Exactly the fixed native recipe was acknowledged before forced success");
        }
        JsonObject proof=args("mode",mode,"boundary",boundary,"halt_exit_code",74,"assertions",assertions,"bridge_artifact_sha256",capabilities.get("bridge_artifact_sha256").getAsString(),"bridge_code_source",Bridge.class.getProtectionDomain().getCodeSource().getLocation().toString(),"java_runtime_version",System.getProperty("java.runtime.version"),"original_operation_id",id,"original_request_id",request.get("request_id").getAsString(),"original_controller_instance_id",request.get("controller_instance_id").getAsString(),"live_operation",live,"live_controller_history",history,"last_real_forced_event",forcedEvent,"live_sequence",sequence,"journal_bytes",journal.length,"journal_sha256",sha(journal),"wire",wire,"physical_qualification",false,"holds_bridge_monitor",true,"native_enabled",machine.isEnabled(),"native_homed",machine.isHomed());
        forcedJson(folder.resolve("crash-proof.json"),proof);System.out.println("CONTROLLER_ACTUAL_HALT "+mode+" exit=74");System.out.flush();Runtime.getRuntime().halt(74);throw new AssertionError("Runtime.halt returned");
    }

    static final class Peer implements AutoCloseable {
        final ServerSocket listener=new ServerSocket();final Thread worker;
        final List<String> commands=new ArrayList<>(),acks=new ArrayList<>();volatile Socket socket;volatile Throwable failure;volatile boolean closing,peerEof;int accepted,commandBytes,responseBytes;String generation;
        Peer()throws Exception{listener.bind(new InetSocketAddress(InetAddress.getByAddress(new byte[]{127,0,0,1}),0),1);worker=new Thread(this::run,"crash-owned-fixed-peer");worker.setDaemon(true);worker.start();}
        synchronized JsonObject snapshot(){return args("accepted_connections",accepted,"command_bytes",commandBytes,"response_bytes",responseBytes,"commands",new ArrayList<>(commands),"acks",new ArrayList<>(acks),"peer_eof",peerEof);}
        void run(){try{
            socket=listener.accept();socket.setSoTimeout(3000);synchronized(this){accepted++;}
            for(int index=1;index<=3;index++){
                ByteArrayOutputStream bytes=new ByteArrayOutputStream();
                for(;;){int value=socket.getInputStream().read();if(value<0)throw new IOException("Early EOF");synchronized(this){if(++commandBytes>512)throw new IOException("Bounded command bytes");}if(value==10)break;if(value<32||value>126||bytes.size()>=511)throw new IOException("Fixed command framing");bytes.write(value);}
                String command=bytes.toString(StandardCharsets.US_ASCII);Matcher match=Pattern.compile("^(G21|G90|M115) ;codex:([a-f0-9]{32}):([1-9][0-9]*)$").matcher(command);
                if(!match.matches()||!List.of("G21","G90","M115").get(index-1).equals(match.group(1))||!Integer.toString(index).equals(match.group(3)))throw new IOException("Unexpected fixed command");
                if(generation==null)generation=match.group(2);if(!generation.equals(match.group(2)))throw new IOException("Unexpected generation");synchronized(this){commands.add(command);}
                if(index==3&&mode.equals("identify-before-ack")){synchronized(bridge){crash("actual-M115-received-before-ACK",channel.lastForced);}throw new AssertionError("Halt returned");}
                String ack="ok codex:"+generation+":"+index+"\n";byte[] response=ack.getBytes(StandardCharsets.US_ASCII);synchronized(this){socket.getOutputStream().write(response);socket.getOutputStream().flush();responseBytes+=response.length;acks.add(ack);}
            }
            int extra=socket.getInputStream().read();if(extra!=-1)throw new IOException("Unexpected extra command");peerEof=true;
        }catch(Throwable problem){if(!closing)failure=problem;}}
        public void close()throws Exception{closing=true;listener.close();if(socket!=null)socket.close();worker.join(1000);}
    }

    /** Only this test replaces the journal field; each observed force reaches the original real FileChannel. */
    static final class ForceObserver extends FileChannel {
        final FileChannel delegate;final ByteArrayOutputStream written=new ByteArrayOutputStream();volatile JsonObject lastForced;
        ForceObserver(FileChannel delegate){this.delegate=delegate;}
        public void force(boolean metadata)throws IOException{
            delegate.force(metadata);if(written.size()==0)return;JsonObject event=JSON.fromJson(written.toString(StandardCharsets.UTF_8),JsonObject.class);written.reset();lastForced=event;
            String type=event.get("type").getAsString();JsonObject p=event.getAsJsonObject("payload");
            boolean hit=mode.equals("connect-intent-force")&&type.equals("controller_diagnostic_step_intent")&&p.get("step").getAsString().equals("connect")||mode.equals("succeeded-force")&&type.equals("operation")&&p.get("state").getAsString().equals("succeeded");
            if(hit)try{crash("real-journal-force-return-before-memory-commit",event);}catch(Exception problem){throw new IOException("Crash fixture boundary failed",problem);}
        }
        public int write(ByteBuffer src)throws IOException{ByteBuffer copy=src.asReadOnlyBuffer();int n=delegate.write(src);byte[] data=new byte[n];copy.get(data);written.write(data);return n;}
        public int read(ByteBuffer dst)throws IOException{return delegate.read(dst);}public long read(ByteBuffer[] dst,int offset,int length)throws IOException{return delegate.read(dst,offset,length);}public int read(ByteBuffer dst,long position)throws IOException{return delegate.read(dst,position);}
        public long write(ByteBuffer[] src,int offset,int length)throws IOException{return delegate.write(src,offset,length);}public int write(ByteBuffer src,long position)throws IOException{return delegate.write(src,position);}
        public long position()throws IOException{return delegate.position();}public FileChannel position(long p)throws IOException{delegate.position(p);return this;}public long size()throws IOException{return delegate.size();}
        public FileChannel truncate(long size)throws IOException{delegate.truncate(size);return this;}public long transferTo(long p,long count,WritableByteChannel target)throws IOException{return delegate.transferTo(p,count,target);}
        public long transferFrom(ReadableByteChannel src,long p,long count)throws IOException{return delegate.transferFrom(src,p,count);}public MappedByteBuffer map(MapMode mode,long p,long size)throws IOException{return delegate.map(mode,p,size);}
        public FileLock lock(long p,long size,boolean shared)throws IOException{return delegate.lock(p,size,shared);}public FileLock tryLock(long p,long size,boolean shared)throws IOException{return delegate.tryLock(p,size,shared);}
        protected void implCloseChannel()throws IOException{delegate.close();}
    }

    public static void main(String[] argv)throws Exception{
        mode=argv[0];folder=Path.of(argv[1]);Path samples=Path.of(argv[2]);check(Set.of("connect-intent-force","identify-before-ack","succeeded-force").contains(mode),"Declared crash seam only");
        Runtime.getRuntime().addShutdownHook(new Thread(()->{try{Files.writeString(folder.resolve("unexpected-shutdown-hook.txt"),"Orderly exit is not the required crash\n",StandardOpenOption.CREATE_NEW);}catch(IOException ignored){}},"fixture-unexpected-orderly-shutdown"));
        try{
            Files.createDirectories(folder.resolve("configuration"));Configuration.initialize(folder.resolve("configuration").toFile());Configuration config=Configuration.get();config.setSystemUnits(LengthUnit.Millimeters);
            peer=new Peer();NativeControllerDiagnostic diagnostic=new NativeControllerDiagnostic(config,TypedGcodeProfile.ownedEndpoint(peer.listener));
            Path token=folder.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);
            bridge=new Bridge(config,token,folder.resolve("journal"),samples,0,true,NativeControllerJournal.PROFILE,null,null,diagnostic);capabilities=call("openpnp_get_capabilities",args());
            JsonObject lease=call("openpnp_request_control_session",args("request_id",UUID.randomUUID().toString(),"ttl_seconds",30));
            request=args("session_id",lease.get("session_id").getAsString(),"request_id",UUID.randomUUID().toString(),"expected_config_revision","cfg-1","controller_instance_id",capabilities.getAsJsonObject("controller_diagnostic").get("controller_instance_id").getAsString());
            forcedJson(folder.resolve("admitted-request.json"),request);
            Field journal=Bridge.class.getDeclaredField("journal");journal.setAccessible(true);channel=new ForceObserver((FileChannel)journal.get(bridge));journal.set(bridge,channel);
            JsonObject accepted=call("openpnp_run_controller_diagnostic",request);String id=accepted.get("operation_id").getAsString();long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(6);
            while(System.nanoTime()<deadline){if(peer.failure!=null)throw new AssertionError("Owned crash peer failed",peer.failure);JsonObject op=call("openpnp_get_operation",args("operation_id",id));if(!Set.of("accepted","running").contains(op.get("state").getAsString()))throw new AssertionError("Operation settled without the required halt: "+op);Thread.sleep(5);}
            throw new AssertionError("Required crash seam never fired");
        }catch(Throwable failure){failure.printStackTrace();forcedJson(folder.resolve("failure.json"),args("mode",mode,"failure",failure.toString(),"assertions",assertions));if(peer!=null)try{peer.close();}catch(Exception ignored){}if(bridge!=null)try{bridge.close();}catch(Exception ignored){}System.exit(1);}
    }
}
