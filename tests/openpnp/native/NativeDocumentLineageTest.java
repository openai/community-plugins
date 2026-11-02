/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.*;
import org.openpnp.model.*;

/** Actual native documents and owned signed store; no machine enable, movement or network. */
public final class NativeDocumentLineageTest {
    static final String A="11111111-1111-4111-8111-111111111111", B="22222222-2222-4222-8222-222222222222";
    static int assertions;static Path root;static Configuration config;static final List<String> groups=new ArrayList<>();
    interface Action{void run()throws Exception;}
    static void check(boolean value,String message){assertions++;if(!value)throw new AssertionError(message);}
    static void expect(String code,Action action)throws Exception{try{action.run();throw new AssertionError("Missing "+code);}catch(Bridge.Fault fault){check(code.equals(fault.code),"Expected "+code+", got "+fault.code);}}
    public static void main(String[] args)throws Exception{
        if(args.length==2){child(args[0],Paths.get(args[1]));return;}
        root=Files.createTempDirectory("native-document-lineage-");Configuration.initialize(root.toFile());config=Configuration.get();config.load();
        NativeJobDocumentsTest.config=config;NativeJobDocumentsTest.part=config.getParts().get(0);
        int exit=0;try{roundTrip();rejectInvalid();rejectTamper();restart();check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"metadata work never enabled or homed machine");System.out.println("OPENPNP_DOCUMENT_LINEAGE_RESULT "+new Gson().toJson(Bridge.map("passed",true,"assertions",assertions,"groups",groups,"physical_qualification",false,"native_motion",false,"durable_association_only",true,"lineage_restart_jvms",2)));}catch(Throwable error){error.printStackTrace();exit=1;}finally{config.getMachine().close();}System.exit(exit);
    }
    static void roundTrip()throws Exception{
        NativeJobDocuments docs=new NativeJobDocuments(config,root.resolve("roundtrip"));Job job=NativeJobDocumentsTest.fixture();Map<String,Object> before=NativeJobDocumentsTest.intent(job);
        NativeJobDocuments.Saved old=docs.save(job);check(!old.manifest.containsKey("lineage"),"legacy save omits lineage rather than inventing it");check(!docs.reloadWithMetadata(old.sha256).lineage.isPresent(),"legacy document remains unknown");check(before.equals(NativeJobDocumentsTest.intent(docs.reload(old.sha256))),"old reload preserves native document semantics");
        NativeJobDocuments.Saved a=docs.save(job,new NativeJobDocuments.LineageMetadata(A,1)),b=docs.save(job,new NativeJobDocuments.LineageMetadata(B,1)),v=docs.save(job,new NativeJobDocuments.LineageMetadata(A,7)),max=docs.save(job,new NativeJobDocuments.LineageMetadata(A,9007199254740991L));
        check(!a.sha256.equals(b.sha256)&&!a.sha256.equals(v.sha256)&&!a.sha256.equals(old.sha256),"lineage identity/revision changes content address");
        Map<String,byte[]> af=unzip(a.bytes),bf=unzip(b.bytes);for(String name:af.keySet())if(!name.equals("manifest.json"))check(Arrays.equals(af.get(name),bf.get(name)),"different lineage leaves exact native asset bytes unchanged");
        check(a.sha256.equals(docs.save(job,new NativeJobDocuments.LineageMetadata(A,1)).sha256),"same job and lineage deduplicate identical archive");
        NativeJobDocuments restarted=new NativeJobDocuments(config,root.resolve("roundtrip"));
        for(NativeJobDocuments.Saved saved:Arrays.asList(a,b,v,max)){
            NativeJobDocuments.Reloaded loaded=restarted.reloadWithMetadata(saved.sha256);check(loaded.lineage.isPresent(),"verified persisted lineage present after helper reconstruction");
            Map<?,?> declared=(Map<?,?>)saved.manifest.get("lineage");check(declared.get("lineage_id").equals(loaded.lineage.get().lineageId),"exact lineage ID round-trip");check(((Number)declared.get("lineage_revision")).longValue()==loaded.lineage.get().lineageRevision,"exact lineage revision round-trip");check(before.equals(NativeJobDocumentsTest.intent(loaded.job)),"lineage preserves native sharing/nested bottom/X-out/placed-history intent");
        }
        a.manifest.put("lineage",Bridge.map("version",99,"lineage_id",B,"lineage_revision",9));
        NativeJobDocuments.Reloaded actual=restarted.reloadWithMetadata(a.sha256);check(actual.lineage.get().lineageId.equals(A)&&actual.lineage.get().lineageRevision==1,"mutable returned display manifest cannot alter verified lineage");
        NativeJobDocuments.Saved resavedUnknown=restarted.save(actual.job);check(!resavedUnknown.manifest.containsKey("lineage")&&!restarted.reloadWithMetadata(resavedUnknown.sha256).lineage.isPresent(),"legacy resave never silently upgrades lineage from loaded Job");
        groups.add("native persisted metadata/history roundtrip; identical assets distinct lineage hashes; old APIs remain unknown; historical revisions accepted without current authority");
    }
    static void rejectInvalid()throws Exception{
        NativeJobDocuments docs=new NativeJobDocuments(config,root.resolve("reject-before-save"));Job job=NativeJobDocumentsTest.fixture();Map<String,String> before=inventory(root.resolve("reject-before-save"));
        for(String id:new String[]{null,"",A.toUpperCase().replace('1','A'),"1-1-1-1-1",A+"x"})expect("DOCUMENT_LINEAGE_INVALID",()->docs.save(job,new NativeJobDocuments.LineageMetadata(id,1)));
        for(long rev:new long[]{Long.MIN_VALUE,-1,0,9007199254740992L,Long.MAX_VALUE})expect("DOCUMENT_LINEAGE_INVALID",()->docs.save(job,new NativeJobDocuments.LineageMetadata(A,rev)));
        check(before.equals(inventory(root.resolve("reject-before-save"))),"invalid typed metadata changes no store/key/archive files");check(job.getFile()==null&&job.getBoardLocations().get(0).getFileName()==null,"invalid typed metadata rejects before native file rebinding");
        groups.add("typed UUID/revision bounds fail before any save publication or graph rebinding");
        NativeJobDocuments.Saved seed=docs.save(job);
        List<String> malformed=Arrays.asList(
            "null","[]","true","\"unknown\"",
            "{\"version\":2,\"lineage_id\":\""+A+"\",\"lineage_revision\":1}",
            "{\"version\":\"1\",\"lineage_id\":\""+A+"\",\"lineage_revision\":1}",
            "{\"version\":1,\"lineage_id\":42,\"lineage_revision\":1}",
            "{\"version\":1,\"lineage_id\":\""+A+"\",\"lineage_revision\":\"1\"}",
            "{\"version\":1,\"lineage_id\":\""+A+"\",\"lineage_revision\":1.5}",
            "{\"version\":1,\"lineage_id\":\""+A+"\",\"lineage_revision\":9007199254740992}",
            "{\"version\":1,\"lineage_id\":\""+A+"\",\"lineage_revision\":0}",
            "{\"version\":1,\"lineage_id\":\"not-a-uuid\",\"lineage_revision\":1}",
            "{\"version\":1,\"lineage_id\":\""+A+"\"}",
            "{\"version\":1,\"lineage_id\":\""+A+"\",\"lineage_revision\":1,\"authority\":true}",
            "{\"version\":1,\"lineage_id\":\""+A+"\",\"lineage_revision\":1,\"lineage_revision\":2}");
        int index=0;for(String value:malformed)malformedOwned(seed,"bad-"+(index++),",\"lineage\":"+value);
        malformedOwned(seed,"duplicate",",\"lineage\":{\"version\":1,\"lineage_id\":\""+A+"\",\"lineage_revision\":1},\"lineage\":{\"version\":1,\"lineage_id\":\""+A+"\",\"lineage_revision\":1}");
        groups.add("test-only signed malformed manifests reject wrong fields/types/version/duplicates before extraction or native load; no public arbitrary-JSON input");
    }
    @SuppressWarnings("unchecked") static void malformedOwned(NativeJobDocuments.Saved seed,String name,String suffix)throws Exception{
        Path storePath=root.resolve(name);new NativeJobDocuments(config,storePath);NativeDocumentStore store=new NativeDocumentStore(storePath);
        Map<String,byte[]> files=unzip(seed.bytes);String raw=new String(files.get("manifest.json"),StandardCharsets.UTF_8);files.put("manifest.json",(raw.substring(0,raw.length()-1)+suffix+"}").getBytes(StandardCharsets.UTF_8));
        byte[] bytes=zip(files);Map<String,String> hashes=new LinkedHashMap<>();for(Map.Entry<String,byte[]> entry:files.entrySet())hashes.put(entry.getKey(),sha(entry.getValue()));
        // Deliberate test-only signer: not a public document metadata API.
        store.save(sha(bytes),bytes,hashes,(Map<String,String>)seed.manifest.get("required_parts"),(Map<String,String>)seed.manifest.get("required_packages"));
        NativeJobDocuments reader=new NativeJobDocuments(config,storePath);Map<String,String> before=inventory(storePath);expect("DOCUMENT_LINEAGE_INVALID",()->reader.reloadWithMetadata(sha(bytes)));check(before.equals(inventory(storePath)),"malformed verified metadata caused no extraction/publication");
    }
    static void rejectTamper()throws Exception{
        Path path=root.resolve("tamper");NativeJobDocuments docs=new NativeJobDocuments(config,path);NativeJobDocuments.Saved saved=docs.save(NativeJobDocumentsTest.fixture(),new NativeJobDocuments.LineageMetadata(A,1));
        Path archive=path.resolve(saved.sha256+".zip");byte[] bytes=saved.bytes.clone();bytes[bytes.length/2]^=1;Files.write(archive,bytes);expect("ARTIFACT_INTEGRITY",()->docs.reloadWithMetadata(saved.sha256));Files.write(archive,saved.bytes);
        Path receipt=path.resolve(saved.sha256+".receipt.json");byte[] original=Files.readAllBytes(receipt);String text=new String(original,StandardCharsets.UTF_8);String bad=text.replaceFirst("hmac_sha256\":\".","hmac_sha256\":\"z");check(!bad.equals(text),"receipt fixture tampered signature");Files.write(receipt,bad.getBytes(StandardCharsets.UTF_8));expect("DOCUMENT_RECEIPT_INVALID",()->docs.reloadWithMetadata(saved.sha256));Files.write(receipt,original);
        check(docs.reloadWithMetadata(saved.sha256).lineage.get().lineageId.equals(A),"verified lineage returns after exact bytes restored");groups.add("archive and signed-receipt tamper refuse lineage and Job; metadata never bypasses owned store verification");
    }
    static void restart()throws Exception{
        Path shared=Files.createDirectory(root.resolve("restart"));
        for(String phase:Arrays.asList("save","reload")){
            Path home=Files.createDirectory(shared.resolve("home-"+phase));Path log=shared.resolve(phase+".log");
            Process process=new ProcessBuilder(Paths.get(System.getProperty("java.home"),"bin","java").toString(),"-Xmx512m","-XX:+ExitOnOutOfMemoryError","-Djava.awt.headless=true","-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory","-Duser.home="+home,"-Djava.io.tmpdir="+home,"--add-opens=java.base/java.lang=ALL-UNNAMED","--add-opens=java.desktop/java.awt=ALL-UNNAMED","--add-opens=java.desktop/java.awt.color=ALL-UNNAMED","-cp",System.getProperty("java.class.path"),NativeDocumentLineageTest.class.getName(),phase,shared.toString()).redirectErrorStream(true).redirectOutput(log.toFile()).start();
            if(!process.waitFor(45,java.util.concurrent.TimeUnit.SECONDS)){process.destroyForcibly();process.waitFor();throw new AssertionError("Lineage child timeout");}
            check(process.exitValue()==0,"separate lineage "+phase+" JVM passed: "+Files.readString(log));
        }
        groups.add("separate native save and reload JVMs preserve verified lineage and actual placed history without loading another structural authority");
    }
    static void child(String phase,Path shared)throws Exception{
        check(java.util.prefs.Preferences.userRoot().getClass().getName().equals("org.openpnp.codex.IsolatedPreferencesFactory$MemoryNode"),"child uses private in-memory preferences");
        Path cfg=shared.resolve("config");Files.createDirectories(cfg);Configuration.initialize(cfg.toFile());config=Configuration.get();config.load();NativeJobDocumentsTest.config=config;NativeJobDocumentsTest.part=config.getParts().get(0);int exit=0;
        try{
            NativeJobDocuments docs=new NativeJobDocuments(config,shared.resolve("documents"),shared);
            if(phase.equals("save")){config.save();NativeJobDocuments.Saved saved=docs.save(NativeJobDocumentsTest.fixture(),new NativeJobDocuments.LineageMetadata(A,17));Files.writeString(shared.resolve("document-id"),saved.sha256,StandardOpenOption.CREATE_NEW);}
            else{NativeJobDocuments.Reloaded loaded=docs.reloadWithMetadata(Files.readString(shared.resolve("document-id")));check(loaded.lineage.get().lineageId.equals(A)&&loaded.lineage.get().lineageRevision==17,"fresh JVM gets exact signed lineage");check(NativeJobDocumentsTest.intent(loaded.job).equals(NativeJobDocumentsTest.intent(NativeJobDocumentsTest.fixture())),"fresh JVM preserves native job intent and placed history");}
            check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"child remains passive");System.out.println("OPENPNP_LINEAGE_CHILD_RESULT "+new Gson().toJson(Bridge.map("phase",phase,"assertions",assertions,"passed",true)));
        }catch(Throwable error){error.printStackTrace();exit=1;}finally{config.getMachine().close();}System.exit(exit);
    }
    static Map<String,String> inventory(Path p)throws Exception{Map<String,String> m=new TreeMap<>();try(java.util.stream.Stream<Path> paths=Files.walk(p)){for(Path f:(Iterable<Path>)paths::iterator)m.put(p.relativize(f).toString(),Files.isDirectory(f)?"directory":sha(Files.readAllBytes(f)));}return m;}
    static Map<String,byte[]> unzip(byte[] bytes)throws Exception{Map<String,byte[]> files=new LinkedHashMap<>();try(ZipInputStream in=new ZipInputStream(new ByteArrayInputStream(bytes))){ZipEntry entry;while((entry=in.getNextEntry())!=null)files.put(entry.getName(),in.readAllBytes());}return files;}
    static byte[] zip(Map<String,byte[]> files)throws Exception{ByteArrayOutputStream bytes=new ByteArrayOutputStream();try(ZipOutputStream out=new ZipOutputStream(bytes)){for(Map.Entry<String,byte[]> entry:files.entrySet()){ZipEntry e=new ZipEntry(entry.getKey());e.setTime(0);out.putNextEntry(e);out.write(entry.getValue());out.closeEntry();}}return bytes.toByteArray();}
    static String sha(byte[] bytes)throws Exception{StringBuilder s=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(bytes))s.append(String.format("%02x",b));return s.toString();}
}
