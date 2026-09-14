/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeReplacementContinuationTest.*;
import java.io.*;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.BasicFileAttributes;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.*;

/** Existing completed document verification through a real continuation permit and native
 * document store. Original fault/wrapper authority is synthetic; no Bridge or job execution. */
public final class NativeContinuationDocumentStorageDriftTest {
    static int assertions,refusals;static final List<Object> cases=new ArrayList<>();
    interface Work {void run()throws Exception;}
    static void check(boolean value,String why){assertions++;if(!value)throw new AssertionError(why);}
    static void reject(Work body)throws Exception {try{body.run();throw new AssertionError("Accepted changed committed document storage");}catch(IOException|Bridge.Fault|NativeJobLineage.Fault expected){assertions++;refusals++;}}
    static String sha(byte[] bytes)throws Exception{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));}
    static Map<String,Object> files(Path directory)throws Exception {
        Map<String,Object> result=new TreeMap<>();try(var stream=Files.walk(directory)){for(Path p:stream.filter(Files::isRegularFile).toList()){
            BasicFileAttributes a=Files.readAttributes(p,BasicFileAttributes.class);result.put(directory.relativize(p).toString(),List.of(sha(Files.readAllBytes(p)),String.valueOf(a.fileKey()),a.size(),a.lastModifiedTime().toString()));
        }}return result;
    }
    static int reloads(NativeReplacementDocuments store)throws Exception {Field field=NativeReplacementDocuments.class.getDeclaredField("reloads");field.setAccessible(true);return field.getInt(store);}
    static Map<String,Object> effects(Fixture f)throws Exception {
        return m("counters",counters(),"configuration_files",configurationFiles(),"material",f.e.material.captureReplacementState(),"boards",f.e.boards.captureReplacementState(f.e.jobId),"lineage",f.e.lineage.captureReplacementState(f.e.jobId),"old_history",new TreeMap<>(f.e.oldJob.getPlacedStatusSnapshot()),"candidate_history",new TreeMap<>(f.e.freshJob.getPlacedStatusSnapshot()),"candidate_mapping",f.e.candidate.mapping(),"library_boards",config.getBoards().size(),"library_panels",config.getPanels().size(),"machine_enabled",config.getMachine().isEnabled(),"machine_homed",config.getMachine().isHomed());
    }
    static void endDecision(Fixture f,Fresh fresh,NativeFaultedJobReplacement.ContinuationPermit permit)throws Exception {
        Map<String,Object> terminal=freshOperation(fresh);terminal.put("state","outcome_unknown");terminal.put("native_completion",m("native_wrapper_completed",true,"native_wrapper_succeeded",false,"physical_outcome_verified",false));
        f.e.append("operation",terminal);f.e.terminalWrappers.add(fresh.operation);fresh.revoke();permit.close();
    }
    static void changeManifest(Path archive)throws Exception {
        Map<String,byte[]> entries=new LinkedHashMap<>();try(ZipInputStream zip=new ZipInputStream(Files.newInputStream(archive))){ZipEntry e;while((e=zip.getNextEntry())!=null)entries.put(e.getName(),zip.readAllBytes());}
        Map<String,Object> manifest=NativeJournalJson.parseObject(new String(entries.get("manifest.json"),StandardCharsets.UTF_8));manifest.put("candidate_history_entries",1);
        entries.put("manifest.json",new com.google.gson.GsonBuilder().serializeNulls().create().toJson(manifest).getBytes(StandardCharsets.UTF_8));
        ByteArrayOutputStream bytes=new ByteArrayOutputStream();try(ZipOutputStream zip=new ZipOutputStream(bytes)){for(var e:entries.entrySet()){ZipEntry entry=new ZipEntry(e.getKey());entry.setTime(0);zip.putNextEntry(entry);zip.write(e.getValue());zip.closeEntry();}}
        Files.write(archive,bytes.toByteArray());
    }
    static void scenario(String origin,String mode)throws Exception {
        int start=assertions;String name="storage-"+origin+"-"+mode;
        try(Fixture f=new Fixture(name,"definition")){
            Path owned=root.resolve(name+"-owned");Files.createDirectory(owned);NativeReplacementDocuments store=new NativeReplacementDocuments(config,owned.resolve("documents"),owned);
            if(origin.equals("original"))task(()->{NativeReplacementDocuments.Context context=new NativeReplacementDocuments.Context(f.e.jobId,f.attempt,f.oldRecovery,f.e.faultCapture.digest);f.e.replacement.recordDocument(f.e.permit,store.save(context,f.e.candidate));return null;});
            f.closeOriginal("outcome_unknown");Fresh decision=f.fresh(f.capture(),"continue-faulted-job-replacement");NativeFaultedJobReplacement.ContinuationPermit permit=decision.begin();
            if(origin.equals("continuation")){
                final var firstPermit=permit;task(()->f.e.replacement.ensureContinuationDocument(firstPermit,store));
                endDecision(f,decision,permit);decision=f.fresh(f.capture(),"continue-faulted-job-replacement");permit=decision.begin();
            }
            Map<String,Object> document=o(f.e.replacement.progress(f.attempt).get("document")),record=o(document.get("record"));String id=(String)record.get("document_id");
            check("completed".equals(document.get("phase")),"Measured path begins with an actual completed "+origin+" document");
            Path archive=owned.resolve("documents").resolve(id+".zip"),receipt=owned.resolve("documents").resolve(id+".receipt.json");
            switch(mode){
                case "unchanged":break;
                case "delete-archive":Files.delete(archive);break;
                case "delete-receipt":Files.delete(receipt);break;
                case "tamper-archive":{byte[] b=Files.readAllBytes(archive);b[b.length/2]^=1;Files.write(archive,b);break;}
                case "tamper-receipt":{byte[] b=Files.readAllBytes(receipt);b[b.length/2]^=1;Files.write(receipt,b);break;}
                case "tamper-manifest":changeManifest(archive);break;
                default:throw new AssertionError(mode);
            }
            Map<String,Object> beforeFiles=files(owned),beforeEffects=effects(f),beforeProgress=f.e.replacement.progress(f.attempt),beforeOriginal=immutableOriginal(f.e);byte[] journal=Files.readAllBytes(f.e.file);int events=f.e.events.size(),reloadCount=reloads(store);final var current=permit;
            try{
                if(mode.equals("unchanged")){Map<String,Object> returned=task(()->f.e.replacement.ensureContinuationDocument(current,store));check(NativeFaultedJobReplacement.same(returned,record),"Existing document returns the exact original forced record");}
                else reject(()->task(()->f.e.replacement.ensureContinuationDocument(current,store)));
                check(beforeFiles.equals(files(owned)),"Verification/refusal preserves all store bytes, inode identities and modification times");
                check(Arrays.equals(journal,Files.readAllBytes(f.e.file))&&events==f.e.events.size(),"Completed document verification appends no journal event");
                check(NativeFaultedJobReplacement.same(beforeEffects,effects(f)),"Verification/refusal changes no counters, configuration, load/lineage/native graph state");
                check(NativeFaultedJobReplacement.same(beforeProgress,f.e.replacement.progress(f.attempt)),"Original document phase and all receipt history remain exact");
                check(NativeFaultedJobReplacement.same(beforeOriginal,immutableOriginal(f.e)),"Original manufacturing outcomes remain exact");
                check(reloadCount==0&&reloads(store)==0,"Verification never invokes document reconstruction/reload");
                check(Boolean.FALSE.equals(f.e.replacement.status().get("execution_authority_restored")),"Document verification restores no execution authority");
                cases.add(m("case",name,"assertions",assertions-start,"expected_refusal",!mode.equals("unchanged")));
            }finally{permit.close();}
        }
    }
    public static void main(String[] args)throws Exception {
        initialize(Path.of(args[0]));Throwable error=null;
        try{for(String origin:List.of("original","continuation"))for(String mode:List.of("unchanged","delete-archive","delete-receipt","tamper-archive","tamper-receipt","tamper-manifest"))scenario(origin,mode);}
        catch(Throwable failure){error=failure;failure.printStackTrace();}
        finally{config.getMachine().close();}
        Map<String,Object> result=m("passed",error==null,"assertions",assertions,"refusals",refusals,"cases",cases,"error",error==null?null:error.toString(),"actual_native_document_save",true,"initial_fault_and_wrapper_authority","synthetic component fixture","actual_native_job_initializations",0,"actual_native_feeds",0,"actual_native_placements",0,"full_bridge_continuation",false,"hardware_qualified",false);
        Files.writeString(root.resolve("proof.json"),new com.google.gson.GsonBuilder().setPrettyPrinting().serializeNulls().create().toJson(result)+"\n",StandardOpenOption.CREATE_NEW);System.out.println("NATIVE_CONTINUATION_DOCUMENT_STORAGE_DRIFT "+new com.google.gson.Gson().toJson(result));if(error!=null)System.exit(1);
    }
}
