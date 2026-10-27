/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.*;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.zip.*;
import org.openpnp.model.*;

/** Abrupt process interruption around real fsync/rename publication, not host power-loss evidence. */
public final class NativeDocumentStoreCrashTest {
    static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    public static void main(String[] args)throws Exception {
        if(args.length==4){NativeJobDocumentsRestartTest.assertChildIsolation(Paths.get(args[1]).resolve("child-"+args[0]+"-"+args[3]));if(args[0].equals("write"))write(Paths.get(args[1]),Paths.get(args[2]),args[3]);else recover(Paths.get(args[1]),Paths.get(args[2]),args[3]);return;}
        Path fixture=Files.createTempDirectory("openpnp-document-publication-");NativeJobDocumentsRestartTest.phase("save",fixture);
        for(String boundary:Arrays.asList("archive-before-rename","archive-after-rename","receipt-before-rename","receipt-after-rename")){
            Path store=fixture.resolve(boundary).resolve("documents");Files.createDirectories(store,PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));
            launch("write",fixture,store,boundary,86);launch("recover",fixture,store,boundary,0);
        }
        System.out.println("OPENPNP_NATIVE_DOCUMENT_CRASH_RESULT {\"passed\":[\"abrupt termination before archive rename retains no committed document\",\"abrupt termination after archive rename leaves an uncommitted archive\",\"abrupt termination before receipt rename never adopts pending receipt\",\"abrupt termination after receipt rename recovers the entire verified native document\"],\"interrupted_processes\":4,\"recovery_processes\":4,\"executed_placements\":0,\"host_power_loss_qualified\":false}");
    }
    static void launch(String mode,Path fixture,Path store,String boundary,int expected)throws Exception {
        Path log=fixture.resolve(mode+"-"+boundary+".log");Path childHome=Files.createDirectory(fixture.resolve("child-"+mode+"-"+boundary));
        Process process=new ProcessBuilder(Paths.get(System.getProperty("java.home"),"bin","java").toString(),"-Xmx512m","-Djava.awt.headless=true","-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory","-Duser.home="+childHome,"-Djava.io.tmpdir="+childHome,"--add-opens=java.base/java.lang=ALL-UNNAMED","--add-opens=java.desktop/java.awt=ALL-UNNAMED","--add-opens=java.desktop/java.awt.color=ALL-UNNAMED","-cp",System.getProperty("java.class.path"),NativeDocumentStoreCrashTest.class.getName(),mode,fixture.toString(),store.toString(),boundary).redirectErrorStream(true).redirectOutput(log.toFile()).start();
        if(!process.waitFor(60,TimeUnit.SECONDS)){process.destroyForcibly();throw new AssertionError("Document publication child timed out");}
        check(process.exitValue()==expected,"Unexpected publication child result at "+mode+"/"+boundary+": "+process.exitValue()+"\n"+Files.readString(log));
    }
    static void write(Path fixture,Path store,String boundary)throws Exception {
        String id=Files.readString(fixture.resolve("saved-sha"));byte[] bytes=Files.readAllBytes(fixture.resolve("journal/documents").resolve(id+".zip"));
        LinkedHashMap<String,String> hashes=new LinkedHashMap<>();JsonObject manifest=null;
        try(ZipInputStream zip=new ZipInputStream(new ByteArrayInputStream(bytes))){ZipEntry entry;while((entry=zip.getNextEntry())!=null){byte[] value=zip.readAllBytes();hashes.put(entry.getName(),sha(value));if(entry.getName().equals("manifest.json"))manifest=new JsonParser().parse(new String(value,java.nio.charset.StandardCharsets.UTF_8)).getAsJsonObject();}}
        check(manifest!=null,"trusted native fixture includes its manifest");
        NativeDocumentStore documents=new NativeDocumentStore(store,(target,renamed)->{
            String name=target.getFileName().toString(),kind=name.endsWith(".zip")?"archive":name.endsWith(".receipt.json")?"receipt":"key";
            if(boundary.equals(kind+(renamed?"-after-rename":"-before-rename")))Runtime.getRuntime().halt(86);
        });
        documents.save(id,bytes,hashes,map(manifest.getAsJsonObject("required_parts")),map(manifest.getAsJsonObject("required_packages")));
        throw new AssertionError("Expected the selected abrupt process interruption");
    }
    static void recover(Path fixture,Path store,String boundary)throws Exception {
        Configuration.initialize(fixture.resolve("config").toFile());Configuration config=Configuration.get();config.load();int exit=0;
        try{
            String id=Files.readString(fixture.resolve("saved-sha"));NativeJobDocuments documents=new NativeJobDocuments(config,store,store.getParent());
            if(boundary.equals("receipt-after-rename")){
                Job job=documents.reload(id);check(job.getBoardLocations().size()==2&&job.retrievePlacedStatus(job.getBoardLocations().get(0),"R1"),"committed native document survives abrupt process interruption intact");
            }else NativeJobDocumentsRestartTest.expect("DOCUMENT_NOT_FOUND",()->documents.reload(id));
            check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"recovery never enables or homes the native machine");
        }catch(Throwable error){error.printStackTrace();exit=1;}finally{config.getMachine().close();}System.exit(exit);
    }
    static LinkedHashMap<String,String> map(JsonObject object){LinkedHashMap<String,String> value=new LinkedHashMap<>();for(Map.Entry<String,JsonElement> entry:object.entrySet())value.put(entry.getKey(),entry.getValue().getAsString());return value;}
    static String sha(byte[] value)throws Exception{StringBuilder out=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(value))out.append(String.format(Locale.ROOT,"%02x",b));return out.toString();}
}
