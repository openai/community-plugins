/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;

/** Actual self-halt; fixture admission, production ledger/native processor and real Bridge recovery. */
public final class NativeJvmHaltLedgerTest {
    static final Gson G=new Gson();static final int HALT=73;static int checks;
    static final Set<String> CASES=new LinkedHashSet<>();static {for(String kind:List.of("feed","pick","release"))for(String event:List.of("intent","outcome"))for(String side:List.of("before","after"))CASES.add(kind+"-"+event+"-"+side);for(String side:List.of("before","after"))CASES.add("checkpoint-"+side);}
    static Path root;static String scenario;static Bridge bridge;
    public static void main(String[] args)throws Exception {
        if(args.length!=4||!CASES.contains(args[1]))throw new IllegalArgumentException("phase case ownedRoot sampleRoot required");root=Path.of(args[2]).toAbsolutePath().normalize();scenario=args[1];Path samples=Path.of(args[3]).toAbsolutePath();int exit=0;
        try{if(args[0].equals("crash"))crash(samples);else if(args[0].equals("recover1")||args[0].equals("recover2"))recover(samples,args[0]);else throw new IllegalArgumentException("phase");}
        catch(Throwable failure){failure.printStackTrace();exit=1;}finally{if(bridge!=null)bridge.close();if(Configuration.isInstanceInitialized())Configuration.get().getMachine().close();}
        System.exit(exit);
    }
    static void crash(Path samples)throws Exception {
        Path configPath=Files.createDirectory(root.resolve("config"),PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));
        Configuration.initialize(configPath.toFile());Configuration config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);SimulatorMain.configureSustainedWorkload(config);SimulatorMain.settleFreshFixture(configPath);config=Configuration.get();config.save();
        Configuration c=config;Machine machine=c.getMachine();Path token=root.resolve("token");write(token,(UUID.randomUUID().toString()+UUID.randomUUID()).getBytes(StandardCharsets.UTF_8));
        bridge=new Bridge(c,token,root.resolve("journal"),samples,0,true,"sustained-workload");Map<String,Object> cap=call("openpnp_get_capabilities"),status=call("openpnp_get_status");String instance=(String)cap.get("bridge_instance_id"),operation=UUID.randomUUID().toString(),request=UUID.randomUUID().toString();long sequence=((Number)status.get("through_sequence")).longValue();bridge.close();bridge=null;
        Part part=c.getPart("R0603-1K");ReferenceTrayFeeder feeder=(ReferenceTrayFeeder)machine.getFeeders().stream().filter(f->f.isEnabled()&&f.getPart()==part).findFirst().orElseThrow();check(feeder.getFeedCount()==0&&!machine.isEnabled()&&!machine.isHomed(),"prerun configuration has no feed/enable/home authority");
        json(root.resolve("fixture.json"),map("case",scenario,"machine_id",cap.get("machine_id"),"original_bridge_instance_id",instance,"operation_id",operation,"request_id",request,"bridge_artifact_sha256",cap.get("bridge_artifact_sha256"),"configuration_sha256",configHashes(),"admission_envelope","test fixture, not production Bridge command admission","board_load_authority","logical-run-scope","physical_load_verified",false,"counter_persistence","saved pre-run simulator values; not crash-time material recovery"));
        machine.submit(()->{machine.setEnabled(true);machine.home();return null;},null,true).get(30,TimeUnit.SECONDS);
        try(Recorder recorder=new Recorder(root.resolve("journal/operations.jsonl"),sequence,instance,machine,feeder)){
            recorder.writeEnvelope("operation",map("operation_id",operation,"request_id",request,"request_digest","fixture-admission-no-replay","method","openpnp_start_job","state","running","bridge_instance_id",instance,"config_revision","cfg-1","accepted_at",Instant.now().toString()));
            machine.submit(()->{Job job=CanonicalJobImporter.load(c,canonical(part));recorder.job=job;NativeActionLedger ledger=new NativeActionLedger(recorder,operation,"jvm-halt-one-placement","cfg-1","fixture-logical-load-1",job);machine.getPnpJobProcessor().initialize(job);long step=0;boolean more;do{try(NativeActionLedger.StepScope scope=ledger.openStep(++step)){more=machine.getPnpJobProcessor().next();scope.complete();}if(step>200)throw new AssertionError("Target halt seam was not reached");}while(more);throw new AssertionError("Native job completed without target halt");},null,false).get(60,TimeUnit.SECONDS);
        }
        throw new AssertionError("Crash phase returned without Runtime.halt");
    }
    static final class Recorder implements NativeActionLedger.Sink,AutoCloseable {
        final FileChannel channel;final Path path;final String instance;final Machine machine;final ReferenceTrayFeeder feeder;long sequence;Job job;
        Recorder(Path path,long sequence,String instance,Machine machine,ReferenceTrayFeeder feeder)throws Exception{this.path=path;this.sequence=sequence;this.instance=instance;this.machine=machine;this.feeder=feeder;channel=FileChannel.open(path,StandardOpenOption.WRITE,StandardOpenOption.APPEND);}
        boolean target(String type,Map<String,Object> payload){if(scenario.startsWith("checkpoint-"))return type.equals("native_placement_checkpoint")&&"native-placement-complete-hook".equals(payload.get("state"));String[] parts=scenario.split("-");return type.equals("native_action_"+parts[1])&&parts[0].equals(payload.get("kind"));}
        @Override public void append(String type,Map<String,Object> payload)throws Exception{boolean target=target(type,payload);if(target&&scenario.endsWith("-before"))halt(type,payload,false);writeEnvelope(type,payload);if(target&&scenario.endsWith("-after"))halt(type,payload,true);}
        void writeEnvelope(String type,Map<String,Object> payload)throws Exception{byte[] bytes=(G.toJson(map("sequence",sequence+1,"bridge_instance_id",instance,"occurred_at",Instant.now().toString(),"type",type,"payload",payload))+"\n").getBytes(StandardCharsets.UTF_8);ByteBuffer buffer=ByteBuffer.wrap(bytes);while(buffer.hasRemaining())channel.write(buffer);channel.force(true);sequence++;}
        void halt(String type,Map<String,Object> payload,boolean committed)throws Exception{
            byte[] prefix=Files.readAllBytes(path);json(root.resolve("halt-observation.json"),map("case",scenario,"halt_exit",HALT,"target_type",type,"target_payload",payload,"target_committed",committed,"last_forced_sequence",sequence,"forced_prefix_bytes",prefix.length,"forced_prefix_sha256",hash(prefix),"native_feed_count",feeder.getFeedCount(),"native_total_feed_count",feeds(machine),"native_nozzle_part",part(machine),"native_placed_count",placed(job),"native_enabled",machine.isEnabled(),"native_homed",machine.isHomed(),"native_busy",machine.isBusy(),"observation_basis","synchronous test-only read on the native executor at the actual production ledger sink","independently_inspected",false,"physical_outcome_verified",false,"configuration_saved_at_halt",false));
            System.out.println("OPENPNP_ACTUAL_JVM_HALT "+scenario+" exit="+HALT);System.out.flush();Runtime.getRuntime().halt(HALT);throw new AssertionError("Runtime.halt returned");
        }
        public void close()throws Exception{channel.close();}
    }
    @SuppressWarnings("unchecked") static void recover(Path samples,String phase)throws Exception {
        Map<String,Object> fixture=read(root.resolve("fixture.json")),observed=read(root.resolve("halt-observation.json"));Path journal=root.resolve("journal/operations.jsonl");byte[] prefix=Files.readAllBytes(journal);long haltBytes=((Number)observed.get("forced_prefix_bytes")).longValue();check(prefix.length>=haltBytes&&hash(Arrays.copyOf(prefix,(int)haltBytes)).equals(observed.get("forced_prefix_sha256")),"halt-time forced prefix retained before recovery");
        check(configHashes().equals(fixture.get("configuration_sha256")),"prerun native configuration untouched by halt/recovery");List<Map<String,Object>> original=records(prefix);List<Map<String,Object>> actions=ledger(original);check(!actions.isEmpty(),"actual native hooks produced ledger records");String opId=(String)fixture.get("operation_id");Map<String,Object> expected=NativeActionLedger.recover(original,opId);
        long targetCount=actions.stream().filter(e->isTarget(e,observed)).count();check(targetCount==(Boolean.TRUE.equals(observed.get("target_committed"))?1:0),"exact before/after force target publication");
        Configuration.initialize(root.resolve("config").toFile());Configuration c=Configuration.get();c.load();Machine machine=c.getMachine();check(feeds(machine)==0&&!machine.isEnabled()&&!machine.isHomed()&&part(machine)==null,"fresh native load starts from saved pre-run counters without authority");
        AtomicInteger feedEvents=new AtomicInteger(),nozzlePartEvents=new AtomicInteger(),machineEvents=new AtomicInteger();
        for(Feeder f:machine.getFeeders())((ReferenceTrayFeeder)f).addPropertyChangeListener("feedCount",event->feedEvents.incrementAndGet());
        for(Head h:machine.getHeads())for(Nozzle n:h.getNozzles())((org.openpnp.machine.reference.ReferenceNozzle)n).addPropertyChangeListener("part",event->nozzlePartEvents.incrementAndGet());
        machine.addListener(new MachineListener.Adapter(){@Override public void machineEnabled(Machine m){machineEvents.incrementAndGet();}@Override public void machineHomed(Machine m,boolean homed){if(homed)machineEvents.incrementAndGet();}@Override public void machineBusy(Machine m,boolean busy){if(busy)machineEvents.incrementAndGet();}@Override public void machineHeadActivity(Machine m,Head h){machineEvents.incrementAndGet();}@Override public void machineActuatorActivity(Machine m,Actuator a){machineEvents.incrementAndGet();}});
        bridge=new Bridge(c,root.resolve("token"),root.resolve("journal"),samples,0,true,"sustained-workload");Map<String,Object> cap=call("openpnp_get_capabilities");check(cap.get("machine_id").equals(fixture.get("machine_id")),"durable machine identity preserved");check(!cap.get("bridge_instance_id").equals(fixture.get("original_bridge_instance_id")),"fresh Bridge instance identity after JVM halt");if(phase.equals("recover2"))check(!cap.get("bridge_instance_id").equals(read(root.resolve("recover1.json")).get("bridge_instance_id")),"second recovery JVM has another Bridge identity");
        Map<String,Object> recovered=call("openpnp_get_operation","operation_id",opId);check(recovered.get("state").equals("outcome_unknown"),"interrupted admitted operation remains unknown");check(G.toJsonTree(recovered.get("native_action_recovery")).equals(G.toJsonTree(expected)),"production Bridge exposes the exact replayed native action facts");check(Boolean.FALSE.equals(expected.get("automatic_replay"))&&Boolean.FALSE.equals(expected.get("physical_effect_verification")),"recovery never claims action replay or physical verification");
        Map<String,Object> request=call("openpnp_get_request_status","request_id",fixture.get("request_id"));check(Boolean.TRUE.equals(request.get("found"))&&opId.equals(((Map<?,?>)request.get("operation")).get("operation_id")),"original fixture request resolves to same operation");
        Map<String,Object> status=call("openpnp_get_status");check("absent".equals(status.get("job_state"))&&!Boolean.TRUE.equals(status.get("native_busy")),"recovery does not construct or run the interrupted job");
        String session=(String)call("openpnp_request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");long beforeSequence=((Number)call("openpnp_get_status").get("through_sequence")).longValue();
        try{call("openpnp_set_machine_enabled","request_id",UUID.randomUUID().toString(),"session_id",session,"expected_config_revision",status.get("config_revision"),"enabled",true);throw new AssertionError("Unknown operation allowed mutation");}catch(Bridge.Fault refusal){check(refusal.code.equals("RECOVERY_REQUIRED"),"new actual mutation stays recovery-fenced");}
        check(((Number)call("openpnp_get_status").get("through_sequence")).longValue()==beforeSequence,"refused mutation admitted no operation/event");
        check(feeds(machine)==0&&!machine.isEnabled()&&!machine.isHomed()&&part(machine)==null,"read/lease/refusal causes zero native replay effects");
        check(feedEvents.get()==0&&nozzlePartEvents.get()==0&&machineEvents.get()==0,"native feed/nozzle/machine listeners observe zero recovery effects or queued native work");
        bridge.close();bridge=null;machine.close();byte[] after=Files.readAllBytes(journal);check(after.length>=prefix.length&&Arrays.equals(prefix,Arrays.copyOf(after,prefix.length)),"entire preceding recovery journal remains an exact prefix");check(G.toJsonTree(ledger(records(after))).equals(G.toJsonTree(actions)),"all original action and checkpoint envelopes retained without new native effects");check(configHashes().equals(fixture.get("configuration_sha256")),"recovering the Bridge never saves reset native counters over source state");
        json(root.resolve(phase+".json"),map("case",scenario,"phase",phase,"assertions",checks,"machine_id",cap.get("machine_id"),"bridge_instance_id",cap.get("bridge_instance_id"),"bridge_artifact_sha256",cap.get("bridge_artifact_sha256"),"operation_state",recovered.get("state"),"native_action_recovery",expected,"halt_native_feed_count",observed.get("native_feed_count"),"halt_native_placed_count",observed.get("native_placed_count"),"reloaded_feed_count",0,"recovery_native_feed_events",feedEvents.get(),"recovery_nozzle_part_events",nozzlePartEvents.get(),"recovery_native_machine_events",machineEvents.get(),"native_action_records",actions.size(),"native_complete_checkpoints",actions.stream().filter(e->{Map<?,?> p=(Map<?,?>)e.get("payload");return "native_placement_checkpoint".equals(e.get("type"))&&"native-placement-complete-hook".equals(p.get("state"));}).count(),"journal_sha256",hash(after),"journal_bytes",after.length,"automatic_replay",false,"independent_physical_inspection",false,"counter_reset_is_authoritative_recovery",false));
        System.out.println("OPENPNP_JVM_HALT_RECOVERY "+phase+" "+scenario+" assertions="+checks);
    }
    static boolean isTarget(Map<String,Object> envelope,Map<String,Object> observation){return envelope.get("type").equals(observation.get("target_type"))&&((Map<?,?>)envelope.get("payload")).get("event_id").equals(((Map<?,?>)observation.get("target_payload")).get("event_id"));}
    static JsonObject canonical(Part part){double h=part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue();return G.toJsonTree(map("schemaVersion",1,"id","actual-jvm-halt","units","mm","coordinateConvention","openpnp-top-view","parts",List.of(map("id",part.getId(),"packageId",part.getPackage().getId(),"heightMm",h)),"boards",List.of(map("id","board","widthMm",20,"heightMm",20,"placements",List.of(map("ref","R1","partId",part.getId(),"packageId",part.getPackage().getId(),"heightMm",h,"x",10,"y",10,"z",0,"rotation",0,"side","top","enabled",true,"type","placement")))),"panels",List.of(),"instances",List.of(map("id","board-1","kind","board","definitionId","board","x",0,"y",0,"z",0,"rotation",0,"side","top","enabled",true)))).getAsJsonObject();}
    static int placed(Job job){int n=0;for(BoardLocation b:job.getBoardLocations())for(Placement p:b.getBoard().getPlacements())if(job.retrievePlacedStatus(b,p.getId()))n++;return n;}
    static long feeds(Machine m){long n=0;for(Feeder f:m.getFeeders())n+=((ReferenceTrayFeeder)f).getFeedCount();return n;}
    static String part(Machine m)throws Exception{Part p=m.getDefaultHead().getDefaultNozzle().getPart();return p==null?null:p.getId();}
    static Map<String,Object> configHashes()throws Exception{Map<String,Object> out=new TreeMap<>();try(var files=Files.list(root.resolve("config"))){for(Path p:(Iterable<Path>)files.filter(p->p.getFileName().toString().endsWith(".xml"))::iterator)out.put(p.getFileName().toString(),hash(Files.readAllBytes(p)));}return out;}
    @SuppressWarnings("unchecked") static Map<String,Object> read(Path p)throws Exception{return G.fromJson(Files.readString(p),Map.class);}
    @SuppressWarnings("unchecked") static List<Map<String,Object>> records(byte[] data){List<Map<String,Object>> out=new ArrayList<>();for(String line:new String(data,StandardCharsets.UTF_8).split("\n"))if(!line.isBlank())out.add(G.fromJson(line,Map.class));return out;}
    static List<Map<String,Object>> ledger(List<Map<String,Object>> records){List<Map<String,Object>> out=new ArrayList<>();for(Map<String,Object> e:records)if(NativeActionLedger.isLedgerEventType(String.valueOf(e.get("type"))))out.add(e);return out;}
    @SuppressWarnings("unchecked") static Map<String,Object> call(String method,Object... pairs)throws Exception{return(Map<String,Object>)bridge.call(method,G.toJsonTree(map(pairs)).getAsJsonObject());}
    static void check(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
    static Map<String,Object> map(Object... args){Map<String,Object> m=new LinkedHashMap<>();for(int i=0;i<args.length;i+=2)m.put((String)args[i],args[i+1]);return m;}
    static String hash(byte[] bytes)throws Exception{return HexFormatCompat.bytes(MessageDigest.getInstance("SHA-256").digest(bytes));}
    static final class HexFormatCompat{static String bytes(byte[] b){StringBuilder s=new StringBuilder();for(byte x:b)s.append(String.format(Locale.ROOT,"%02x",x));return s.toString();}}
    static void json(Path p,Map<String,Object> value)throws Exception{write(p,new GsonBuilder().serializeNulls().create().toJson(value).getBytes(StandardCharsets.UTF_8));}
    static void write(Path p,byte[] bytes)throws Exception{Files.createFile(p,PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));try(FileChannel c=FileChannel.open(p,StandardOpenOption.WRITE)){ByteBuffer data=ByteBuffer.wrap(bytes);while(data.hasRemaining())c.write(data);c.force(true);}}
}
