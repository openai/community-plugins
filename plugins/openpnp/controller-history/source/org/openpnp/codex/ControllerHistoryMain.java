/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import java.io.*;
import java.math.BigDecimal;
import java.nio.*;
import java.nio.channels.*;
import java.nio.charset.*;
import java.nio.file.*;
import java.nio.file.attribute.BasicFileAttributes;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;

/** Offline inspection only. Never constructs Bridge, Configuration, a machine or a socket. */
public final class ControllerHistoryMain {
    private static final long MAX_BYTES=64L*1024*1024;
    private static final int MAX_RECORD=1024*1024,MAX_RECORDS=100000,MAX_NODES=50000;
    private static final Gson JSON=new Gson();
    private static final Set<String> STATES=Set.of("accepted","running","paused","succeeded","failed","outcome_unknown","aborted","cancelled");
    private static final Set<String> PASSIVE=Set.of("session_receipt","session_granted","session_released","session_expired");
    private static final String METHOD="openpnp_run_controller_diagnostic";
    private static final class Rejected extends Exception {final String code;Rejected(String code){super(code);this.code=code;}}
    private static void need(boolean condition,String code)throws Rejected{if(!condition)throw new Rejected(code);}
    private static Map<String,Object> map(Object... pairs){Map<String,Object> result=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)result.put((String)pairs[i],pairs[i+1]);return result;}
    @SuppressWarnings("unchecked") private static Map<String,Object> object(Object value)throws Rejected{need(value instanceof Map,"INVALID_OBJECT");return(Map<String,Object>)value;}
    private static String text(Object value)throws Rejected{need(value instanceof String,"INVALID_STRING");return(String)value;}
    private static String uuid(Object value)throws Rejected{String s=text(value);try{need(UUID.fromString(s).toString().equals(s),"INVALID_IDENTITY");}catch(IllegalArgumentException bad){throw new Rejected("INVALID_IDENTITY");}return s;}
    private static long integer(Object value)throws Rejected{need(value instanceof Number,"INVALID_INTEGER");try{long n=new BigDecimal(value.toString()).longValueExact();need(n>=0&&n<=9007199254740991L,"INVALID_INTEGER");return n;}catch(ArithmeticException bad){throw new Rejected("INVALID_INTEGER");}}
    /** Controller schema numbers are integers; reject fractional values before reducer validation. */
    private static void exactNumbers(Object value)throws Rejected{
        if(value instanceof Number)integer(value);
        else if(value instanceof Map)for(Object child:((Map<?,?>)value).values())exactNumbers(child);
        else if(value instanceof List)for(Object child:(List<?>)value)exactNumbers(child);
    }
    private static String hex(byte[] bytes){StringBuilder s=new StringBuilder();for(byte b:bytes)s.append(String.format("%02x",b&255));return s.toString();}
    private static String fileHash(Path path)throws Exception{MessageDigest hash=MessageDigest.getInstance("SHA-256");long count=0;try(InputStream in=Files.newInputStream(path)){byte[] b=new byte[65536];for(int n;(n=in.read(b))!=-1;){need((count+=n)<=MAX_BYTES,"FILE_SIZE_LIMIT");hash.update(b,0,n);}}return hex(hash.digest());}
    private static String classHash(Class<?> type)throws Exception{MessageDigest h=MessageDigest.getInstance("SHA-256");try(InputStream in=type.getResourceAsStream("/"+type.getName().replace('.','/')+".class")){need(in!=null,"PROVENANCE_UNAVAILABLE");byte[] b=new byte[65536];for(int n;(n=in.read(b))!=-1;)h.update(b,0,n);}return hex(h.digest());}
    private static void regular(Path path)throws Exception{for(Path p=path;p!=null;p=p.getParent())need(!Files.isSymbolicLink(p),"SYMLINK_REJECTED");need(Files.isRegularFile(path,LinkOption.NOFOLLOW_LINKS),"REGULAR_FILE_REQUIRED");}

    /** Strict token walk prevents Gson's lenient parser and duplicate-key overwrite behavior. */
    private static final class Parser {
        private int nodes;
        Object read(JsonReader reader,int depth)throws Exception{
            need(depth<=64&&++nodes<=MAX_NODES,"JSON_LIMIT");
            JsonToken token=reader.peek();
            if(token==JsonToken.BEGIN_OBJECT){Map<String,Object> result=new LinkedHashMap<>();reader.beginObject();while(reader.hasNext()){String key=reader.nextName();need(key.length()<=1024&&!result.containsKey(key),"DUPLICATE_OR_INVALID_KEY");result.put(key,read(reader,depth+1));}reader.endObject();return result;}
            if(token==JsonToken.BEGIN_ARRAY){List<Object> result=new ArrayList<>();reader.beginArray();while(reader.hasNext())result.add(read(reader,depth+1));reader.endArray();return result;}
            if(token==JsonToken.STRING){String s=reader.nextString();need(s.length()<=65536,"STRING_LIMIT");return s;}
            if(token==JsonToken.NUMBER){String s=reader.nextString();need(s.length()<=128&&s.matches("-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?"),"INVALID_NUMBER");return new BigDecimal(s).stripTrailingZeros();}
            if(token==JsonToken.BOOLEAN)return reader.nextBoolean();
            if(token==JsonToken.NULL){reader.nextNull();return null;}
            throw new Rejected("INVALID_JSON_TOKEN");
        }
    }
    private static Map<String,Object> parse(byte[] bytes)throws Exception{
        String source=StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
        need(!source.isBlank()&&!source.startsWith("\ufeff"),"EMPTY_OR_BOM_RECORD");
        try(JsonReader reader=new JsonReader(new StringReader(source))){reader.setLenient(false);Object value=new Parser().read(reader,0);need(reader.peek()==JsonToken.END_DOCUMENT,"TRAILING_JSON");return object(value);}
    }
    private static final class Scan {
        final NativeControllerJournal reducer=new NativeControllerJournal();
        final Map<String,Map<String,Object>> operations=new LinkedHashMap<>();
        final Map<String,Map<String,Object>> sessionReceipts=new LinkedHashMap<>();
        Map<String,Object> initialOperation,lastOperation,retirement;
        long sequence,controllerEvents;
        String operationId,requestId;
        boolean completeRecipe()throws Rejected{
            Map<String,Object> h=reducer.snapshot();
            List<?> steps=(List<?>)h.get("steps");
            return reducer.hasHistory()&&retirement!=null&&Boolean.TRUE.equals(retirement.get("completed_recipe"))&&Boolean.TRUE.equals(h.get("retired"))&&h.get("pending")==null&&steps.size()==4&&steps.stream().allMatch(s->Boolean.TRUE.equals(((Map<?,?>)s).get("native_returned")));
        }
        boolean wrapperSuccess(Map<String,Object> operation)throws Exception{
            Object raw=operation.get("native_completion");if(raw==null)return false;
            Map<String,Object> c=object(raw);
            need(c.keySet().equals(Set.of("submission_id","phase","native_wrapper_completed","native_wrapper_succeeded","physical_outcome_verified")),"INVALID_COMPLETION_RECEIPT");
            uuid(c.get("submission_id"));need(c.get("native_wrapper_completed") instanceof Boolean&&c.get("native_wrapper_succeeded") instanceof Boolean&&Boolean.FALSE.equals(c.get("physical_outcome_verified")),"INVALID_COMPLETION_RECEIPT");
            String phase=text(c.get("phase"));need(Set.of("completed","wrapper-failed","cancelled-ownership-retained").contains(phase),"INVALID_COMPLETION_RECEIPT");
            return Boolean.TRUE.equals(c.get("native_wrapper_completed"))&&Boolean.TRUE.equals(c.get("native_wrapper_succeeded"))&&"completed".equals(phase);
        }
        void requireSuccess(Map<String,Object> operation)throws Exception{
            need(completeRecipe()&&wrapperSuccess(operation),"SUCCESS_WITHOUT_COMPLETE_WRAPPER_RECORD");
            reducer.finishRecovery(operation);
            Map<String,Object> result=object(operation.get("result"));
            need(result.keySet().equals(Set.of("controller_instance_id","profile","completed_recipe","steps_attempted","outcome_unknown","observation","physical_qualification","physical_standstill_verified","motion_completion_observed")),"INVALID_SUCCESS_RESULT");
            need(Objects.equals(result.get("controller_instance_id"),initialOperation.get("controller_instance_id"))&&NativeControllerJournal.PROFILE.equals(result.get("profile"))&&Boolean.TRUE.equals(result.get("completed_recipe"))&&integer(result.get("steps_attempted"))==4&&Boolean.FALSE.equals(result.get("outcome_unknown")),"CONTRADICTORY_SUCCESS_RESULT");
            for(String key:List.of("physical_qualification","physical_standstill_verified","motion_completion_observed"))need(Boolean.FALSE.equals(result.get(key)),"PHYSICAL_CLAIM_REJECTED");
            need(Objects.equals(result.get("observation"),retirement.get("resource_teardown")),"SUCCESS_OBSERVATION_CONFLICT");
        }
        void accept(Map<String,Object> event)throws Exception{
            need(event.keySet().equals(Set.of("sequence","bridge_instance_id","occurred_at","type","payload")),"INVALID_ENVELOPE");
            need(integer(event.get("sequence"))==++sequence&&sequence<=MAX_RECORDS,"SEQUENCE_OR_RECORD_LIMIT");
            uuid(event.get("bridge_instance_id"));Instant.parse(text(event.get("occurred_at")));
            String type=text(event.get("type"));Map<String,Object> payload=object(event.get("payload"));
            if(type.equals("operation")){
                need(METHOD.equals(payload.get("method")),"UNSUPPORTED_OPERATION_HISTORY");
                String id=uuid(payload.get("operation_id")),request=uuid(payload.get("request_id"));
                uuid(payload.get("controller_instance_id"));uuid(payload.get("bridge_instance_id"));
                need(text(payload.get("request_digest")).matches("[a-f0-9]{64}")&&text(payload.get("config_revision")).matches("cfg-[0-9]+"),"INVALID_OPERATION_BINDING");
                Instant.parse(text(payload.get("accepted_at")));
                if(payload.containsKey("updated_at"))Instant.parse(text(payload.get("updated_at")));
                else need("accepted".equals(payload.get("state")),"MISSING_OPERATION_UPDATE_TIME");
                String state=text(payload.get("state"));need(STATES.contains(state),"INVALID_OPERATION_STATE");
                wrapperSuccess(payload);
                if(initialOperation==null){need(state.equals("accepted"),"MISSING_OPERATION_ADMISSION");initialOperation=new LinkedHashMap<>(payload);operationId=id;requestId=request;}
                else{
                    need(operationId.equals(id)&&requestId.equals(request),"MULTIPLE_OR_CHANGED_OPERATION");
                    for(String key:List.of("operation_id","request_id","request_digest","method","bridge_instance_id","config_revision","controller_instance_id","accepted_at"))need(Objects.equals(initialOperation.get(key),payload.get(key)),"OPERATION_IDENTITY_CONFLICT");
                    // Exact recovery transitions (including conservative abandonment) are enforced by the installed reducer.
                }
                reducer.observeRecoveredOperation(payload,operations.get(id));
                if(state.equals("succeeded"))requireSuccess(payload);
                lastOperation=payload;operations.put(id,payload);return;
            }
            if(NativeControllerJournal.matches(type)){
                need(initialOperation!=null,"MISSING_OPERATION_ADMISSION");
                need((type.equals("controller_diagnostic_admission")?"accepted":"running").equals(lastOperation.get("state")),"CONTROLLER_EVENT_OUTSIDE_OPERATION_PHASE");
                exactNumbers(payload);reducer.acceptRecovered(type,payload,operations);
                if(type.equals("controller_diagnostic_retired"))retirement=payload;
                controllerEvents++;return;
            }
            need(PASSIVE.contains(type),"UNSUPPORTED_JOURNAL_EVENT");
            if(type.equals("session_receipt")){
                need(payload.keySet().equals(Set.of("request_id","request_digest","session_id","ownership_epoch","bridge_instance_id","state","ttl_seconds")),"INVALID_SESSION_RECEIPT");
                String request=text(payload.get("request_id"));need(!request.isEmpty()&&request.length()<=1024,"INVALID_SESSION_RECEIPT");
                uuid(payload.get("session_id"));uuid(payload.get("bridge_instance_id"));need(text(payload.get("request_digest")).matches("[a-f0-9]{64}"),"INVALID_SESSION_RECEIPT");
                long ttl=integer(payload.get("ttl_seconds"));need(ttl>=1&&ttl<=600&&integer(payload.get("ownership_epoch"))>=1,"INVALID_SESSION_RECEIPT");
                String state=text(payload.get("state"));need(Set.of("accepted","succeeded","outcome_unknown").contains(state),"INVALID_SESSION_RECEIPT");
                Map<String,Object> prior=sessionReceipts.get(request);
                if(prior==null){need(state.equals("accepted")&&sessionReceipts.size()<1000,"SESSION_PREFIX_OR_LIMIT");}
                else{for(String key:List.of("request_digest","session_id","ownership_epoch","bridge_instance_id","ttl_seconds"))need(Objects.equals(prior.get(key),payload.get(key)),"SESSION_IDENTITY_CONFLICT");need(prior.get("state").equals(state)||prior.get("state").equals("accepted")&&!state.equals("accepted"),"SESSION_STATE_REGRESSION");}
                sessionReceipts.put(request,payload);
            }else if(type.equals("session_granted")){
                need(payload.keySet().equals(Set.of("session_id","ownership_epoch")),"INVALID_SESSION_EVENT");uuid(payload.get("session_id"));integer(payload.get("ownership_epoch"));
                need(sessionReceipts.values().stream().anyMatch(s->Objects.equals(s.get("session_id"),payload.get("session_id"))&&Objects.equals(s.get("ownership_epoch"),payload.get("ownership_epoch"))&&s.get("state").equals("accepted")),"SESSION_GRANT_WITHOUT_RECEIPT");
            }else{need(payload.keySet().equals(Set.of("ownership_epoch"))&&integer(payload.get("ownership_epoch"))>=1,"INVALID_SESSION_EVENT");}
        }
        Map<String,Object> summary()throws Exception{
            need(initialOperation!=null,"NO_CONTROLLER_HISTORY");
            if(reducer.hasHistory())reducer.finishRecovery(lastOperation);
            String recorded=(String)lastOperation.get("state");Map<String,Object> history=reducer.snapshot();
            @SuppressWarnings("unchecked") List<Map<String,Object>> steps=(List<Map<String,Object>>)history.get("steps");
            boolean complete=completeRecipe(),wrapper=wrapperSuccess(lastOperation);
            if(recorded.equals("succeeded"))requireSuccess(lastOperation);
            List<Object> safeSteps=new ArrayList<>();for(Map<String,Object> step:steps)safeSteps.add(map("step",step.get("step"),"step_index",step.get("step_index"),"dispatched",step.get("dispatched"),"native_returned",step.get("native_returned")));
            String pending=null;if(history.get("pending") instanceof Map)pending=(String)object(history.get("pending")).get("step");
            return map("profile",NativeControllerJournal.PROFILE,"operation_id",operationId,"request_id",requestId,"controller_instance_id",initialOperation.get("controller_instance_id"),"recorded_operation_state",recorded,
                "offline_disposition",recorded.equals("succeeded")?"recorded-success":Set.of("accepted","running","paused","outcome_unknown").contains(recorded)?"outcome-unknown":"recorded-"+recorded,
                "requires_reconciliation",!recorded.equals("succeeded"),"controller_admission_recorded",reducer.hasHistory(),"controller_event_count",controllerEvents,"steps",safeSteps,"pending_step",pending,"retirement_recorded",history.get("retired"),"complete_recipe_recorded",complete,"wrapper_success_recorded",wrapper);
        }
    }
    private static Map<String,Object> inspect(Path path)throws Exception{
        regular(path);BasicFileAttributes before=Files.readAttributes(path,BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS);
        need(before.size()>0&&before.size()<=MAX_BYTES,"JOURNAL_SIZE_LIMIT");
        try(FileChannel channel=FileChannel.open(path,StandardOpenOption.READ,LinkOption.NOFOLLOW_LINKS)){
            FileLock acquired;try{acquired=channel.tryLock(0,Long.MAX_VALUE,true);}catch(OverlappingFileLockException|NonReadableChannelException failure){throw new Rejected("JOURNAL_IN_USE");}
            need(acquired!=null,"JOURNAL_IN_USE");
            try(FileLock lock=acquired){
                MessageDigest hash=MessageDigest.getInstance("SHA-256");ByteBuffer buffer=ByteBuffer.allocate(65536);ByteArrayOutputStream line=new ByteArrayOutputStream();Scan scan=new Scan();long count=0;
                while(channel.read(buffer)!=-1){buffer.flip();while(buffer.hasRemaining()){byte value=buffer.get();hash.update(value);need(++count<=MAX_BYTES,"JOURNAL_SIZE_LIMIT");if(value==10){scan.accept(parse(line.toByteArray()));line.reset();}else{need(line.size()<MAX_RECORD,"RECORD_SIZE_LIMIT");line.write(value);}}buffer.clear();}
                need(line.size()==0,"INCOMPLETE_JSONL_TAIL");String originalHash=hex(hash.digest());Map<String,Object> summary=scan.summary();
                channel.position(0);MessageDigest second=MessageDigest.getInstance("SHA-256");long again=0;while(channel.read(buffer)!=-1){buffer.flip();again+=buffer.remaining();need(again<=MAX_BYTES,"JOURNAL_CHANGED");second.update(buffer);buffer.clear();}
                regular(path);BasicFileAttributes after=Files.readAttributes(path,BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS);
                need(lock.isValid()&&count==before.size()&&again==count&&after.size()==before.size()&&Objects.equals(before.fileKey(),after.fileKey())&&before.lastModifiedTime().equals(after.lastModifiedTime())&&originalHash.equals(hex(second.digest())),"JOURNAL_CHANGED");
                return map("schema_version",1,"mode","offline-recorded-controller-history","valid",true,"journal_sha256",originalHash,"journal_bytes",count,"records",scan.sequence,"input_unchanged",true,"shared_read_lock_held_during_scan",true,"history",summary,
                    "native_authority_restored",false,"native_machine_opened",false,"controller_connection_opened",false,"journal_appended",false,"recorded_bytes_prove_force_or_power_loss_survival",false,"physical_qualification",false);
            }
        }
    }
    public static void main(String[] args){
        try{
            need(args.length==8&&args[0].equals("--journal")&&args[2].equals("--expected-reducer-sha256")&&args[4].equals("--expected-journal-json-sha256")&&args[6].equals("--expected-gson-sha256"),"USAGE");
            need(args[3].matches("[a-f0-9]{64}")&&args[3].equals(classHash(NativeControllerJournal.class)),"INCOMPATIBLE_REDUCER");
            need(args[5].matches("[a-f0-9]{64}")&&args[5].equals(classHash(NativeJournalJson.class)),"INCOMPATIBLE_JOURNAL_JSON");
            need(NativeControllerJournal.class.getProtectionDomain().getCodeSource().getLocation().equals(NativeJournalJson.class.getProtectionDomain().getCodeSource().getLocation()),"INCOMPATIBLE_JOURNAL_JSON_ORIGIN");
            Path gson=Paths.get(Gson.class.getProtectionDomain().getCodeSource().getLocation().toURI());
            need(args[7].matches("[a-f0-9]{64}")&&args[7].equals(fileHash(gson)),"INCOMPATIBLE_GSON");Path path=Paths.get(args[1]);need(path.isAbsolute(),"ABSOLUTE_PATH_REQUIRED");path=path.normalize();
            Map<String,Object> result=inspect(path);
            Path bridge=Paths.get(NativeControllerJournal.class.getProtectionDomain().getCodeSource().getLocation().toURI());
            result.put("provenance",map("bridge_sha256",fileHash(bridge),"gson_sha256",fileHash(gson),"reducer_class_sha256",classHash(NativeControllerJournal.class),"journal_json_class_sha256",classHash(NativeJournalJson.class),"inspector_class_sha256",classHash(ControllerHistoryMain.class),"upstream_commit","5bd404cfc70f34103a3ca0fbb6b50c2b465f407c"));
            System.out.println(JSON.toJson(result));
        }catch(Throwable failure){String code=failure instanceof Rejected?((Rejected)failure).code:"INVALID_OR_UNAVAILABLE_HISTORY";System.out.println(JSON.toJson(map("schema_version",1,"mode","offline-recorded-controller-history","valid",false,"error_code",code,"native_authority_restored",false,"native_machine_opened",false,"controller_connection_opened",false,"journal_appended",false,"physical_qualification",false)));System.exit(2);}
    }
}
