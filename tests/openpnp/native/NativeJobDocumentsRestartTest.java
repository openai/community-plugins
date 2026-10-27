/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.io.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.model.Abstract2DLocatable.Side;

/** Uses distinct native JVMs: no retained in-memory receipt survives the tested restart. */
public final class NativeJobDocumentsRestartTest {
    interface Action {void run()throws Exception;}
    static void check(boolean valid,String message){if(!valid)throw new AssertionError(message);}
    static void expect(String code,Action action)throws Exception{try{action.run();throw new AssertionError("Expected "+code);}catch(Bridge.Fault fault){if(!code.equals(fault.code))throw new AssertionError("Expected "+code+", got "+fault.code,fault);}}
    public static void main(String[] args)throws Exception {
        if(args.length>0){child(args[0],Paths.get(args[1]));return;}
        Path root=Files.createTempDirectory("openpnp-document-restart-");
        phase("save",root);phase("reload",root);phase("boundaries",root);phase("limit",root);
        System.out.println("OPENPNP_NATIVE_DOCUMENT_RESTART_RESULT {\"passed\":[\"separate native JVM reload preserves document and placed history\",\"signed receipts reject corruption and foreign stores before native XML\",\"orphan publication remains uncommitted\",\"persistent reload and storage limits survive helper reopen\"],\"native_jvm_phases\":4,\"executed_placements\":0,\"hardware_qualified\":false}");
    }
    static void phase(String phase,Path root)throws Exception {
        Path childHome=Files.createDirectory(root.resolve("child-"+phase));
        String jvmExecutable=Paths.get(System.getProperty("java.home"),"bin","java").toString();
        Process process=new ProcessBuilder(jvmExecutable,"-Xmx512m","-Djava.awt.headless=true","-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory","-Duser.home="+childHome,"-Djava.io.tmpdir="+childHome,"--add-opens=java.base/java.lang=ALL-UNNAMED","--add-opens=java.desktop/java.awt=ALL-UNNAMED","--add-opens=java.desktop/java.awt.color=ALL-UNNAMED","-cp",System.getProperty("java.class.path"),NativeJobDocumentsRestartTest.class.getName(),phase,root.toString()).redirectErrorStream(true).start();
        ByteArrayOutputStream captured=new ByteArrayOutputStream();Thread reader=new Thread(()->{try{process.getInputStream().transferTo(captured);}catch(IOException ignored){}});reader.start();
        if(!process.waitFor(60,java.util.concurrent.TimeUnit.SECONDS)){process.destroyForcibly();throw new AssertionError("Native document child phase timed out: "+phase);}
        reader.join();Files.write(root.resolve(phase+".child.log"),captured.toByteArray(),StandardOpenOption.CREATE_NEW);if(process.exitValue()!=0)throw new AssertionError("Native document child "+phase+" failed:\n"+captured.toString(java.nio.charset.StandardCharsets.UTF_8));
    }
    static void assertChildIsolation(Path expectedHome)throws Exception {
        check("org.openpnp.codex.IsolatedPreferencesFactory".equals(System.getProperty("java.util.prefs.PreferencesFactory")),"child explicitly selects the isolated preferences factory before native load");
        check(Files.isSameFile(expectedHome,Paths.get(System.getProperty("user.home")))&&Files.isSameFile(expectedHome,Paths.get(System.getProperty("java.io.tmpdir"))),"child home and temporary paths are owned fixture directories");
        java.util.prefs.Preferences preferences=java.util.prefs.Preferences.userRoot();
        check("org.openpnp.codex.IsolatedPreferencesFactory$MemoryNode".equals(preferences.getClass().getName()),"actual child preferences root is memory-only");
        check(preferences.get("child-isolation-probe",null)==null,"fresh child has no preceding child preference state");
        preferences.put("child-isolation-probe","memory-only");
        System.out.println("OPENPNP_CHILD_JVM_ISOLATION_RESULT {\"preferences\":\"fresh-in-memory\",\"private_home\":true,\"private_temporary_directory\":true,\"native_load_started\":false}");
    }
    static void child(String phase,Path root)throws Exception {
        assertChildIsolation(root.resolve("child-"+phase));
        Files.createDirectories(root.resolve("config"));Files.createDirectories(root.resolve("journal"));
        Configuration.initialize(root.resolve("config").toFile());Configuration config=Configuration.get();config.load();int exit=0;
        try {
            Path store=root.resolve("journal/documents");
            if(phase.equals("save")){
                config.save();Part part=config.getParts().get(0);Board board=new Board();board.setName("restart-shared-board");board.setDimensions(new Location(LengthUnit.Millimeters,20,10,0,0));
                Placement placement=new Placement("R1");placement.setPart(part);placement.setLocation(new Location(LengthUnit.Millimeters,3,4,0,35));placement.setSide(Side.Top);board.addPlacement(placement);
                Placement bottom=new Placement("R2");bottom.setPart(part);bottom.setLocation(new Location(LengthUnit.Millimeters,7,2,0,-45));bottom.setSide(Side.Bottom);board.addPlacement(bottom);
                Job job=new Job();BoardLocation first=new BoardLocation(new Board(board));first.setId("A");first.setLocation(new Location(LengthUnit.Millimeters,100,120,3,15));job.addBoardOrPanelLocation(first);
                BoardLocation second=new BoardLocation(new Board(board));second.setId("B");second.setSide(Side.Bottom);second.setLocallyEnabled(false);job.addBoardOrPanelLocation(second);job.storePlacedStatus(first,"R1",true);
                NativeJobDocuments.Saved saved=new NativeJobDocuments(config,store,root.resolve("journal")).save(job);Files.writeString(root.resolve("saved-sha"),saved.sha256,StandardOpenOption.CREATE_NEW);
                check(Files.isRegularFile(store.resolve(saved.sha256+".receipt.json")),"durable signed receipt was published");
            }else if(phase.equals("reload")){
                String id=Files.readString(root.resolve("saved-sha"));Job job=new NativeJobDocuments(config,store,root.resolve("journal")).reload(id);
                check(job.getBoardLocations().size()==2,"both native board instances survive restart");BoardLocation first=job.getBoardLocations().get(0),second=job.getBoardLocations().get(1);
                check(first.getBoard().getDefinition()==second.getBoard().getDefinition(),"shared definition identity survives restart");
                check(job.retrievePlacedStatus(first,"R1")&&!job.retrievePlacedStatus(second,"R1"),"per-instance placed history survives restart");
                check(first.getLocation().equals(new Location(LengthUnit.Millimeters,100,120,3,15))&&second.getSide()==Side.Bottom&&!second.isEnabled(),"pose, bottom side and X-out survive restart");
                check(first.getPlacementsTransformStatus()==PlacementsHolderLocation.PlacementsTransformStatus.NotSet,"registration remains invalidated after restart");
            }else if(phase.equals("boundaries")){
                String id=Files.readString(root.resolve("saved-sha"));Path receipt=store.resolve(id+".receipt.json"),archive=store.resolve(id+".zip");byte[] original=Files.readAllBytes(receipt);
                byte[] altered=original.clone();altered[altered.length-4]^=1;Files.write(receipt,altered);
                expect("DOCUMENT_RECEIPT_INVALID",()->new NativeJobDocuments(config,store,root.resolve("journal")));Files.write(receipt,original);
                byte[] bytes=Files.readAllBytes(archive),damaged=bytes.clone();damaged[damaged.length/2]^=1;Files.write(archive,damaged);
                expect("ARTIFACT_INTEGRITY",()->new NativeJobDocuments(config,store,root.resolve("journal")));Files.write(archive,bytes);
                Path key=store.resolve("receipt-key");Files.setPosixFilePermissions(key,java.nio.file.attribute.PosixFilePermissions.fromString("rw-r--r--"));expect("DOCUMENT_RECEIPT_KEY_INVALID",()->new NativeJobDocuments(config,store,root.resolve("journal")));Files.setPosixFilePermissions(key,java.nio.file.attribute.PosixFilePermissions.fromString("rw-------"));
                Path retainedKey=root.resolve("retained-receipt-key");Files.move(key,retainedKey);expect("DOCUMENT_RECEIPT_KEY_MISSING",()->new NativeJobDocuments(config,store,root.resolve("journal")));check(!Files.exists(key),"missing key is never silently regenerated beside committed receipts");Files.move(retainedKey,key);
                Path foreign=root.resolve("foreign/documents");Files.createDirectories(foreign);Files.copy(receipt,foreign.resolve(receipt.getFileName()));Files.copy(archive,foreign.resolve(archive.getFileName()));
                expect("DOCUMENT_RECEIPT_KEY_MISSING",()->new NativeJobDocuments(config,foreign,foreign.getParent()));
                Path orphan=root.resolve("orphan/documents");Files.createDirectories(orphan);Files.copy(archive,orphan.resolve(archive.getFileName()));
                NativeJobDocuments uncommitted=new NativeJobDocuments(config,orphan,orphan.getParent());expect("DOCUMENT_NOT_FOUND",()->uncommitted.reload(id));
                try(RandomAccessFile file=new RandomAccessFile(orphan.resolve("0".repeat(64)+".zip").toFile(),"rw")){file.setLength(NativeDocumentStore.MAX_ARCHIVES_BYTES-bytes.length);}
                NativeJobDocuments fullOrphanStore=new NativeJobDocuments(config,orphan,orphan.getParent());Job another=new NativeJobDocuments(config,store,root.resolve("journal")).reload(id);another.getBoardLocations().get(0).setLocation(new Location(LengthUnit.Millimeters,101,120,3,15));
                expect("DOCUMENT_CAPACITY",()->fullOrphanStore.save(another));check(Files.size(orphan.resolve("0".repeat(64)+".zip"))==NativeDocumentStore.MAX_ARCHIVES_BYTES-bytes.length,"uncommitted archive bytes are counted and never silently removed");
                Files.createSymbolicLink(store.resolve("external-link"),archive);expect("PATH_REJECTED",()->new NativeJobDocuments(config,store,root.resolve("journal")));Files.delete(store.resolve("external-link"));
                NativeJobDocuments documents=new NativeJobDocuments(config,store,root.resolve("journal"));expect("DOCUMENT_NOT_FOUND",()->documents.reload("../arbitrary.xml"));
                Part part=config.getParts().get(0);Length old=part.getHeight();part.setHeight(new Length(99,LengthUnit.Millimeters));expect("DOCUMENT_DEPENDENCY_CHANGED",()->documents.reload(id));part.setHeight(old);
            }else if(phase.equals("limit")){
                String id=Files.readString(root.resolve("saved-sha"));long current;try(java.util.stream.Stream<Path> paths=Files.list(store)){current=paths.filter(p->p.getFileName().toString().startsWith("load-")).count();}
                for(long i=current;i<32;i++)new NativeJobDocuments(config,store,root.resolve("journal")).reload(id);
                NativeJobDocuments exhausted=new NativeJobDocuments(config,store,root.resolve("journal"));expect("DOCUMENT_CAPACITY",()->exhausted.reload(id));
                Path sparse=store.resolve("uncommitted-bytes");try(RandomAccessFile file=new RandomAccessFile(sparse.toFile(),"rw")){file.setLength(NativeDocumentStore.MAX_STORE_BYTES);}
                expect("DOCUMENT_CAPACITY",()->new NativeJobDocuments(config,store,root.resolve("journal")));Files.delete(sparse);
            }else throw new AssertionError("Unknown child phase");
            check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"document operations never enable/home native machine");
        }catch(Throwable failure){failure.printStackTrace();exit=1;}finally{config.getMachine().close();}System.exit(exit);
    }
}
