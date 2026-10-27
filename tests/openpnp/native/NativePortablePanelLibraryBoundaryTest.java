/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.awt.geom.AffineTransform;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.model.Abstract2DLocatable.Side;
import org.openpnp.model.PlacementsHolderLocation.PlacementsTransformStatus;
import org.openpnp.spi.*;
import org.openpnp.util.Utils2D;
import org.w3c.dom.*;
import static org.openpnp.codex.NativePortableConfiguration.*;

/** Actual native library refusals. Fault fixtures never acquire machine/job authority. */
public final class NativePortablePanelLibraryBoundaryTest {
    static Path root; static Configuration c; static Board board; static Panel panel;
    static int assertions, refusals, enables, homes, headEvents;
    static final List<Map<String,Object>> cases = new ArrayList<>();
    static final List<Path> sourceFiles = new ArrayList<>();
    interface Checked { void run() throws Exception; }
    static void check(boolean condition,String label) { assertions++; if(!condition) throw new AssertionError(label); }
    static byte[] bytes(Path path)throws Exception { return read(path,MAX_FILE); }
    static Map<String,String> sourceHashes()throws Exception {
        Map<String,String> hashes=new TreeMap<>();
        for(Path p:sourceFiles) hashes.put(root.relativize(p).toString(),sha(bytes(p)));
        return hashes;
    }
    static void reject(String label,Checked call)throws Exception {
        try { call.run(); throw new AssertionError("Unexpected acceptance: "+label); }
        catch(Bridge.Fault fault) { refusals++; cases.add(Bridge.map("label",label,"code",fault.code,"refused",true)); }
    }
    static void exportRefusal(String label)throws Exception {
        Map<String,String> before=sourceHashes();Path archive=root.resolve("refused-"+UUID.randomUUID()+".zip");
        reject(label,()->export(c,archive));
        check(!Files.exists(archive),label+": no published archive");
        check(before.equals(sourceHashes()),label+": saved source bytes unchanged");
        check(!c.getMachine().isEnabled()&&!c.getMachine().isHomed(),label+": machine disabled/unhomed");
    }
    static Location loc(double x,double y,double r) { return new Location(LengthUnit.Millimeters,x,y,0,r); }
    static BoardLocation child(Board definition,String id) {
        BoardLocation child=new BoardLocation(new Board(definition)); child.setId(id);
        child.setLocation(loc(0,0,0)); child.setSide(Side.Top);child.setLocallyEnabled(true);child.setCheckFiducials(false);
        return child;
    }
    static BoardLocation child(String id) { List<PlacementsHolderLocation<?>> matches=new ArrayList<>();for(PlacementsHolderLocation<?> value:panel.getChildren())if(id.equals(value.getId()))matches.add(value);check(matches.size()==1,"Exact unique native child identity "+id);return (BoardLocation)matches.get(0); }
    static Placement placement(String id,Part part,Placement.Type type) {
        Placement p=new Placement(id);p.setPart(part);p.setType(type);p.setSide(Side.Top);p.setLocation(loc(5,6,0));return p;
    }
    static void fixture()throws Exception {
        root=Files.createTempDirectory("portable-panel-boundaries-");Path config=root.resolve("config");
        Configuration.initialize(config.toFile());c=Configuration.get();c.load();SimulatorMain.accelerateFixture(c);SimulatorMain.settleFreshFixture(config);c=Configuration.get();
        c.getMachine().addListener(new MachineListener.Adapter(){
            public void machineEnabled(Machine m){enables++;}
            public void machineHomed(Machine m,boolean homed){if(homed)homes++;}
            public void machineHeadActivity(Machine m,Head h){headEvents++;}
        });
        Part resistor=c.getPart("R0805-1K"),fiducial=c.getPart("FIDUCIAL-1X2-FIDUCIAL1X2");
        check(resistor!=null&&fiducial!=null,"Pinned native Part identities exist");
        board=c.getBoard(root.resolve("source.board.xml").toFile());board.setName("boundary-board");board.setDimensions(loc(30,20,0));
        board.addPlacement(placement("R1",resistor,Placement.Type.Placement));board.addPlacement(placement("FID",fiducial,Placement.Type.Fiducial));c.saveBoard(board);
        panel=new Panel();panel.setName("boundary-panel");panel.setDimensions(loc(80,50,0));panel.setFile(root.resolve("source.panel.xml").toFile());
        panel.addChild(child(board,"A"));BoardLocation b=child(board,"B");b.setLocation(loc(40,0,90));panel.addChild(b);
        panel.addPlacement(placement("OWN-FID",fiducial,Placement.Type.Fiducial));c.addPanel(panel);c.savePanel(panel);c.save();
        sourceFiles.add(board.getFile().toPath());sourceFiles.add(panel.getFile().toPath());for(String name:CONFIG)sourceFiles.add(config.resolve(name));
    }
    static void sourceCases()throws Exception {
        String savedName=panel.getName();panel.setName("unsaved");exportRefusal("dirty panel");check(panel.isDirty(),"Dirty refusal retains flag");panel.setName(savedName);panel.setDirty(false);
        Placement drift=placement("UNSAVED",c.getPart("R0805-1K"),Placement.Type.Fiducial);panel.getPlacements().add(drift);
        check(!panel.isDirty(),"Native mutable placements list bypasses dirty flag");exportRefusal("dirty-false own placement drift");panel.getPlacements().remove(drift);
        BoardLocation a=child("A");Location original=a.getLocation();a.setLocation(loc(1,0,0));panel.setDirty(false);exportRefusal("dirty-false child pose drift");a.setLocation(original);panel.setDirty(false);
        panel.setProfile(panel.getProfile());panel.setDirty(false);exportRefusal("explicit default-shaped outline");panel.setProfile(null);panel.setDirty(false);
        panel.columns=2;panel.rows=3;panel.setDirty(false);exportRefusal("native public legacy rows and columns");check(panel.columns==2&&panel.rows==3,"Refusal occurs before native Persist erases legacy data");panel.columns=null;panel.rows=null;
        AffineTransform nominal=Utils2D.getDefaultBoardPlacementLocationTransform(a);a.setLocalToParentTransform(new AffineTransform(nominal));panel.setDirty(false);
        check(a.getPlacementsTransformStatus()==PlacementsTransformStatus.NotSet,"Nominal native transform cache is unregistered");
        Path nominalArchive=root.resolve("nominal-cache.zip");export(c,nominalArchive);check(Files.isRegularFile(nominalArchive),"Nominal transform cache alone is supported");
        AffineTransform altered=new AffineTransform(nominal);altered.translate(3,4);a.setLocalToParentTransform(altered);panel.setDirty(false);
        check(a.getPlacementsTransformStatus()==PlacementsTransformStatus.NotSet,"Direct native setter leaves custom transform status NotSet");exportRefusal("non-nominal transform disguised as NotSet");a.setLocalToParentTransform(null);panel.setDirty(false);
        a.setLocalToGlobalTransform(new AffineTransform(nominal));panel.setDirty(false);exportRefusal("locally registered source child");a.setLocalToParentTransform(null);panel.setDirty(false);
        PanelLocation parent=new PanelLocation(new Panel());parent.setLocalToGlobalTransform(new AffineTransform());a.setParent(parent);panel.setDirty(false);
        exportRefusal("source child inherits external registration and parent");a.setParent(null);panel.setDirty(false);
        BoardLocation replaced=child(board,"A");replaced.getPlaced().put("orphan",false);panel.getChildren().set(0,replaced);panel.setDirty(false);
        exportRefusal("false native legacy placed-map entry");check(Boolean.FALSE.equals(replaced.getPlaced().get("orphan")),"Refusal retains legacy false entry before native Persist");panel.getChildren().set(0,a);
        Placement onCopy=a.getBoard().getPlacements().get("R1");Part actual=onCopy.getPart(),shadow=new Part(actual.getId());shadow.setPackage(actual.getPackage());onCopy.setPart(shadow);panel.setDirty(false);
        exportRefusal("foreign child Part object with same ID");onCopy.setPart(actual);panel.setDirty(false);
        String filename=a.getFileName();a.setFileName(root.resolve("missing/source.board.xml").toString());panel.setDirty(false);
        exportRefusal("native child basename fallback reference");a.setFileName(filename);panel.setDirty(false);
        File panelFile=panel.getFile();Path relocated=root.resolve("relocated.panel.xml");Files.copy(panelFile.toPath(),relocated);panel.setFile(relocated.toFile());panel.setDirty(false);
        exportRefusal("panel registry key and model file identity drift");panel.setFile(panelFile);panel.setDirty(false);
        PanelLocation nested=new PanelLocation(new Panel());nested.setId("NESTED");panel.getChildren().add(nested);panel.setDirty(false);
        exportRefusal("nested PanelLocation hidden behind clean flag");panel.getChildren().remove(nested);
        BoardLocation duplicate=child(board,"A");panel.getChildren().add(duplicate);panel.setDirty(false);exportRefusal("duplicate direct child ID without addChild renaming");panel.getChildren().remove(duplicate);
        String id=a.getId();for(String bad:List.of("bad⇒child","bad\u0085child","x".repeat(129))) {a.setId(bad);panel.setDirty(false);exportRefusal("invalid source child ID "+Integer.toHexString(bad.hashCode()));}a.setId(id);panel.setDirty(false);
        a.setId("bad\ud800");panel.setDirty(false);exportRefusal("malformed surrogate child identity");a.setId(id);panel.setDirty(false);
        Placement own=panel.getPlacements().get("OWN-FID");own.setType(Placement.Type.Placement);panel.setDirty(false);exportRefusal("own executable panel component");own.setType(Placement.Type.Fiducial);panel.setDirty(false);
    }

    static void pseudoSourceCases()throws Exception {
        Placement pseudo=panel.createPseudoPlacement("A⇒FID");panel.addPseudoPlacement(pseudo);c.savePanel(panel);c.save();
        check(panel.getPseudoPlacementIds().equals(List.of("A⇒FID")),"Saved pseudo has exact native direct target");
        Path accepted=root.resolve("pseudo-control.zip");export(c,accepted);check(Files.isRegularFile(accepted),"Native derived pseudo positive control");
        BoardLocation originalChild=child("A"),replacement=child(board,"A");panel.getChildren().set(0,replacement);panel.setDirty(false);check(panel.getDescendantPlacement("A⇒FID").first.get(0)==replacement,"Current exact pseudo path resolves replacement child");check(originalChild.isListener("side",pseudo)&&!replacement.isListener("side",pseudo),"Actual native pseudo still observes detached original child");exportRefusal("stale pseudo listener after byte-equivalent native child replacement");panel.getChildren().set(0,originalChild);panel.setDirty(false);
        String comment=pseudo.getComments();pseudo.setComments("unsaved override");panel.setDirty(false);exportRefusal("pseudo comment override lost by ID-only persistence");pseudo.setComments(comment);panel.setDirty(false);
        boolean enabled=pseudo.isEnabled();pseudo.setEnabled(!enabled);panel.setDirty(false);exportRefusal("pseudo enabled override lost by ID-only persistence");pseudo.setEnabled(enabled);panel.setDirty(false);
        int rank=pseudo.getRank();pseudo.setRank(rank+1);panel.setDirty(false);exportRefusal("pseudo rank override lost by ID-only persistence");pseudo.setRank(rank);panel.setDirty(false);
        Location before=pseudo.getLocation();pseudo.setLocation(before.add(loc(0.1,0,0)));panel.setDirty(false);exportRefusal("pseudo derived pose override");pseudo.setLocation(before);panel.setDirty(false);
        panel.getPseudoPlacementIds().add("B⇒MISSING");check(!panel.isDirty(),"Direct pseudo ID list mutation bypasses dirty state");exportRefusal("unresolved ID erased by Panel.persist");check(panel.getPseudoPlacementIds().contains("B⇒MISSING"),"ID refusal precedes native Persist mutation");panel.getPseudoPlacementIds().remove("B⇒MISSING");
        panel.getPseudoPlacements().remove(pseudo);panel.setDirty(false);exportRefusal("saved pseudo missing from native derived objects");panel.getPseudoPlacements().add(pseudo);panel.setDirty(false);
        byte[] saved=bytes(panel.getFile().toPath());Document d=xml(saved);Element child=(Element)d.getElementsByTagName("object").item(0);child.setAttribute("file-name",root.resolve("not-present/source.board.xml").toString());Files.write(panel.getFile().toPath(),encode(d));
        try {exportRefusal("saved child reference requires native basename fallback");} finally {Files.write(panel.getFile().toPath(),saved);}
    }
    interface XmlEdit {void run(Document doc)throws Exception;}
    static Map<String,byte[]> changePanel(Map<String,byte[]> base,XmlEdit edit)throws Exception {
        Map<String,byte[]> changed=copyEntries(base);JsonObject m=object(base.get("manifest.json"));JsonObject row=m.getAsJsonObject("panel_library").getAsJsonArray("panels").get(0).getAsJsonObject();String old=row.get("entry").getAsString();
        Document doc=xml(changed.remove(old));edit.run(doc);byte[] content=encode(doc);check(!Arrays.equals(content,base.get(old)),"Adversarial XML edit actually changes bytes");String digest=sha(content),entry="library/panels/panel-001-"+digest+".panel.xml";
        changed.put(entry,content);row.addProperty("entry",entry);row.addProperty("sha256",digest);row.addProperty("semantic_sha256",semantic(doc));changed.put("manifest.json",JSON.toJson(m).getBytes(StandardCharsets.UTF_8));return changed;
    }
    static Element firstChild(Document doc){return (Element)doc.getElementsByTagName("object").item(0);}
    static void xmlRefusal(Map<String,byte[]> entries,String label,XmlEdit edit)throws Exception {admissionRefusal(changePanel(entries,edit),label,true);check(Set.of("PORTABLE_PANEL_LIBRARY","PORTABLE_BOARD_LIBRARY","PORTABLE_PROFILE_LIMIT").contains(cases.get(cases.size()-1).get("code")),label+": refused by native content policy after integrity checks");}
    static void xmlCases(Map<String,byte[]> entries)throws Exception {
        xmlRefusal(entries,"authenticated arbitrary native child class",d->firstChild(d).setAttribute("class","org.openpnp.model.PanelLocation"));
        xmlRefusal(entries,"authenticated unknown child field",d->firstChild(d).setAttribute("unknown-state","false"));
        xmlRefusal(entries,"authenticated legacy panel columns",d->{Element x=d.createElement("columns");x.setTextContent("2");d.getDocumentElement().appendChild(x);});
        xmlRefusal(entries,"authenticated old panel version",d->d.getDocumentElement().setAttribute("version","1.1"));
        xmlRefusal(entries,"authenticated duplicate dimensions",d->d.getDocumentElement().appendChild(d.getElementsByTagName("dimensions").item(0).cloneNode(true)));
        xmlRefusal(entries,"authenticated child placed history",d->{Element x=d.createElement("placed");firstChild(d).appendChild(x);});
        xmlRefusal(entries,"authenticated missing explicit enabled",d->firstChild(d).removeAttribute("locally-enabled"));
        xmlRefusal(entries,"authenticated nonboolean check-fiducials",d->firstChild(d).setAttribute("check-fiducials","0"));
        xmlRefusal(entries,"authenticated duplicate child identity",d->((Element)d.getElementsByTagName("object").item(1)).setAttribute("id","A"));
        xmlRefusal(entries,"authenticated external child file",d->firstChild(d).setAttribute("file-name","/tmp/source.board.xml"));
        xmlRefusal(entries,"authenticated unbound board identity",d->firstChild(d).setAttribute("file-name","codex-board:board-032"));
        xmlRefusal(entries,"authenticated child coordinate overflow",d->((Element)firstChild(d).getElementsByTagName("location").item(0)).setAttribute("x","1000.00001"));
        xmlRefusal(entries,"authenticated nonfinite child coordinate",d->((Element)firstChild(d).getElementsByTagName("location").item(0)).setAttribute("rotation","NaN"));
        xmlRefusal(entries,"authenticated dangling canonical Part",d->((Element)d.getElementsByTagName("placement").item(0)).setAttribute("part-id","ABSENT-PART"));
        xmlRefusal(entries,"authenticated native case-insensitive Part alias",d->{Element x=(Element)d.getElementsByTagName("placement").item(0);x.setAttribute("part-id",x.getAttribute("part-id").toLowerCase(Locale.ROOT));});
        xmlRefusal(entries,"authenticated own executable panel record",d->((Element)d.getElementsByTagName("placement").item(0)).setAttribute("type","Placement"));
        xmlRefusal(entries,"authenticated dangling pseudo leaf",d->d.getElementsByTagName("string").item(0).setTextContent("A⇒ABSENT"));
        xmlRefusal(entries,"authenticated partial-prefix pseudo path",d->d.getElementsByTagName("string").item(0).setTextContent("AFID"));
        xmlRefusal(entries,"authenticated duplicate pseudo identity",d->{Node x=d.getElementsByTagName("string").item(0);x.getParentNode().appendChild(x.cloneNode(true));});
        xmlRefusal(entries,"authenticated descendant pseudo path",d->d.getElementsByTagName("string").item(0).setTextContent("A⇒NESTED⇒FID"));
        JsonObject manifest=object(entries.get("manifest.json"));String entry=NativePortablePanelLibrary.records(manifest).get(0).get("entry").getAsString();Map<String,Document> boards=NativePortablePanelLibrary.boardDocs(entries,manifest);Set<String> parts=NativePortableBoardLibrary.partIds(entries);
        reject("cumulative panel element budget",()->NativePortablePanelLibrary.validatePanel(xml(entries.get(entry)),boards,parts,new int[]{0,50000,0,0}));
        reject("cumulative panel text budget",()->NativePortablePanelLibrary.validatePanel(xml(entries.get(entry)),boards,parts,new int[]{0,0,2*1024*1024,0}));
        Document repeated=xml(entries.get(entry));Element group=(Element)repeated.getElementsByTagName("children").item(0),template=firstChild(repeated);while(group.hasChildNodes())group.removeChild(group.getFirstChild());
        for(int i=0;i<1001;i++){Element x=(Element)template.cloneNode(true);x.setAttribute("id","D"+i);x.setAttribute("locally-enabled","false");group.appendChild(x);}
        reject("1001 disabled children still consume holder budget",()->NativePortablePanelLibrary.validatePanel(repeated,boards,parts,new int[4]));
        Document expanded=xml(entries.get(entry));Map<String,Document> largeBoards=new LinkedHashMap<>(boards);Document large=xml(encode(boards.get("board-001")));Element placements=(Element)large.getElementsByTagName("placements").item(0),p=(Element)placements.getElementsByTagName("placement").item(0);while(placements.hasChildNodes())placements.removeChild(placements.getFirstChild());
        for(int i=0;i<5001;i++){Element x=(Element)p.cloneNode(true);x.setAttribute("id","R"+i);placements.appendChild(x);}largeBoards.put("board-001",large);
        reject("repeated-board expansion exceeds10000 before native copies",()->NativePortablePanelLibrary.validatePanel(expanded,largeBoards,parts,new int[4]));
    }
    static Map<String,byte[]> copyEntries(Map<String,byte[]> entries) { return new TreeMap<>(entries); }
    static void resignOuter(Map<String,byte[]> entries)throws Exception {
        JsonObject manifest=object(entries.get("manifest.json"));Map<String,String> inventory=hashes(entries);inventory.remove("manifest.json");manifest.add("entry_sha256",JSON.toJsonTree(inventory));
        for(String name:CONFIG)manifest.getAsJsonObject("native_xml_semantic_sha256").addProperty(name,semantic(xml(entries.get("config/"+name))));
        entries.put("manifest.json",JSON.toJson(manifest).getBytes(StandardCharsets.UTF_8));
    }
    static void admissionRefusal(Map<String,byte[]> entries,String label,boolean outerHashes)throws Exception {
        Map<String,byte[]> changed=copyEntries(entries);if(outerHashes)resignOuter(changed);byte[] content=zip(changed);
        Path archive=root.resolve("malformed-"+UUID.randomUUID()+".zip"),destination=root.resolve("refused-adoption-"+UUID.randomUUID());Files.write(archive,content);
        Configuration before=Configuration.get();Map<String,String> source=sourceHashes();reject(label,()->prepare(archive,sha(content),destination));
        check(!Files.exists(destination),label+": refused before reservation");check(Configuration.get()==before,label+": no native singleton replacement");check(source.equals(sourceHashes()),label+": source bytes unchanged");
    }
    static void archiveCases(Map<String,byte[]> entries)throws Exception {
        JsonObject manifest=object(entries.get("manifest.json"));check(manifest.get("version").getAsInt()==3,"Supported source exported as v3");
        String panelEntry=entries.keySet().stream().filter(x->x.startsWith("library/panels/")).findFirst().orElseThrow();
        Map<String,byte[]> missing=copyEntries(entries);missing.remove(panelEntry);admissionRefusal(missing,"missing panel with recomputed outer inventory",true);
        Map<String,byte[]> extra=copyEntries(entries);extra.put("library/panels/panel-032-"+"a".repeat(64)+".panel.xml",entries.get(panelEntry));admissionRefusal(extra,"unreferenced extra panel resource",true);
        String registry=new String(entries.get("config/panels.xml"),StandardCharsets.UTF_8);
        Map<String,byte[]> external=copyEntries(entries);external.put("config/panels.xml",registry.replace("codex-panel:panel-001","/tmp/source.panel.xml").getBytes(StandardCharsets.UTF_8));admissionRefusal(external,"external panel registry path with recomputed semantics",true);
        Map<String,byte[]> duplicate=copyEntries(entries);Document duplicateRegistry=xml(entries.get("config/panels.xml"));Element registryRoot=duplicateRegistry.getDocumentElement();registryRoot.appendChild(registryRoot.getElementsByTagName("panel").item(0).cloneNode(true));duplicate.put("config/panels.xml",encode(duplicateRegistry));admissionRefusal(duplicate,"duplicate registry reference",true);
        Map<String,byte[]> traversal=copyEntries(entries);traversal.put("library/panels/../escape.panel.xml",entries.get(panelEntry));admissionRefusal(traversal,"traversal archive member",true);
        for(String field:List.of("panel_count","child_count","placement_count","pseudo_count","expanded_record_count")) {
            Map<String,byte[]> fraction=copyEntries(entries);JsonObject m=object(entries.get("manifest.json"));m.getAsJsonObject("panel_library").addProperty(field,new java.math.BigDecimal("1.00000000000000000000001"));fraction.put("manifest.json",JSON.toJson(m).getBytes(StandardCharsets.UTF_8));admissionRefusal(fraction,"exact fractional "+field,true);
        }
        Map<String,byte[]> wrongBool=copyEntries(entries);JsonObject m=object(entries.get("manifest.json"));m.addProperty("physical_state_transferred","false");wrongBool.put("manifest.json",JSON.toJson(m).getBytes(StandardCharsets.UTF_8));admissionRefusal(wrongBool,"nonboolean authority flag",true);
        Map<String,byte[]> invalidUtf8=copyEntries(entries);byte[] rawUtf8=entries.get("manifest.json").clone();int at=-1;for(int i=0;i<rawUtf8.length;i++)if(rawUtf8[i]=='f'){at=i;break;}check(at>=0,"UTF8 mutation byte found");rawUtf8[at]=(byte)0xff;invalidUtf8.put("manifest.json",rawUtf8);byte[] malformedZip=zip(invalidUtf8);Path malformedArchive=root.resolve("invalid-utf8.zip"),malformedDestination=root.resolve("invalid-utf8-adoption");Files.write(malformedArchive,malformedZip);boolean decoderRefused=false;try{prepare(malformedArchive,sha(malformedZip),malformedDestination);}catch(java.nio.charset.CharacterCodingException expected){decoderRefused=true;}catch(Bridge.Fault expected){decoderRefused=true;}check(decoderRefused&&!Files.exists(malformedDestination)&&Configuration.get()==c,"Invalid UTF8 rejected before reservation/native initialization");
        Map<String,byte[]> duplicateKey=copyEntries(entries);String raw=new String(entries.get("manifest.json"),StandardCharsets.UTF_8);duplicateKey.put("manifest.json",raw.replaceFirst("\\{","{\"version\":3,").getBytes(StandardCharsets.UTF_8));admissionRefusal(duplicateKey,"duplicate manifest object key",false);
    }

    static void runChild(String mode,Path target)throws Exception {
        Path log=root.resolve(mode+".log");List<String> argv=new ArrayList<>(List.of(Paths.get(System.getProperty("java.home"),"bin/java").toString(),"-Xmx2g","-XX:+ExitOnOutOfMemoryError","-Djava.awt.headless=true","-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory","-Duser.home="+root,"-Djava.io.tmpdir="+root,"--add-opens=java.base/java.lang=ALL-UNNAMED","--add-opens=java.desktop/java.awt=ALL-UNNAMED","--add-opens=java.desktop/java.awt.color=ALL-UNNAMED","-cp",System.getProperty("java.class.path"),NativePortablePanelLibraryBoundaryTest.class.getName(),mode,target.toString()));
        Process child=new ProcessBuilder(argv).redirectErrorStream(true).redirectOutput(log.toFile()).start();boolean timedOut=!child.waitFor(60,java.util.concurrent.TimeUnit.SECONDS);if(timedOut){child.destroy();if(!child.waitFor(5,java.util.concurrent.TimeUnit.SECONDS)){child.destroyForcibly();child.waitFor();}}
        Map<String,Object> lifecycle=Bridge.map("mode",mode,"pid",child.pid(),"exited",!child.isAlive(),"exit_code",child.exitValue(),"timed_out",timedOut,"log_sha256",sha(read(log,MAX_FILE)),"log",log.toString());Files.writeString(root.resolve(mode+"-lifecycle.json"),JSON.toJson(lifecycle));check(!timedOut&&child.exitValue()==0,"Fresh native child "+mode+" passed; retained log="+log);check(Files.readString(log).contains("OPENPNP_PORTABLE_PANEL_BOUNDARY_CHILD"),"Child emitted bounded completion receipt");cases.add(lifecycle);
    }
    static void stagingCases(Path archive,Map<String,byte[]> entries)throws Exception {
        String digest=sha(read(archive,MAX_BUNDLE));JsonObject manifest=object(entries.get("manifest.json"));String entry=NativePortablePanelLibrary.records(manifest).get(0).get("entry").getAsString();Map<String,String> source=sourceHashes();
        Prepared changed=prepare(archive,digest,root.resolve("tampered-stage"));Files.write(changed.staging.resolve(entry),new byte[]{'\n'},StandardOpenOption.APPEND);reject("late prepared panel tamper before Configuration.initialize",()->validateAndPublish(changed));check(Configuration.get()==c&&!Files.exists(changed.destination.resolve("adoption.json")),"Staging tamper changes no native singleton/activation");
        Prepared linked=prepare(archive,digest,root.resolve("symlink-stage"));Path target=linked.staging.resolve(entry);Files.delete(target);Files.createSymbolicLink(target,panel.getFile().toPath());reject("symlinked staged panel before Configuration.initialize",()->validateAndPublish(linked));check(Configuration.get()==c&&!Files.exists(linked.destination.resolve("adoption.json")),"Symlink stage changes no native singleton/activation");
        Path interrupted=root.resolve("interrupted-stage");boolean interruptedOnce=false;try{prepare(archive,digest,interrupted,(count,stage)->{if(Files.exists(stage.resolve(entry)))throw new java.io.IOException("test-only panel staging interruption");});}catch(java.io.IOException expected){interruptedOnce=expected.getMessage().equals("test-only panel staging interruption");}check(interruptedOnce&&!Files.exists(interrupted.resolve("adoption.json")),"Interrupted panel staging publishes no activation");
        for(String mode:List.of("missing-panel","corrupt-panel","native-fallback")){Prepared p=prepare(archive,digest,root.resolve(mode));runChild(mode,p.staging);}
        runChild("activation-tamper",archive);check(Configuration.get()==c&&source.equals(sourceHashes()),"All fresh child probes retain source singleton and exact saved bytes");
    }
    static void childMain(String mode,Path input)throws Exception {
        Configuration loaded=null;try {
            if(mode.equals("activation-tamper")){
                Prepared prepared=prepare(input,sha(read(input,MAX_BUNDLE)),input.getParent().resolve("activation-child"));Map<String,Object> result=validateAndPublish(prepared);loaded=Configuration.get();check(Boolean.TRUE.equals(result.get("adopted")),"Fresh native adoption positive control");Path configuration=activeConfiguration(prepared.destination);JsonObject m=object(read(prepared.staging.resolve("manifest.json"),256*1024));String entry=NativePortablePanelLibrary.records(m).get(0).get("entry").getAsString();Path panelPath=prepared.staging.resolve(entry);byte[] before=read(panelPath,MAX_FILE);Files.write(panelPath,new byte[]{'\n'},StandardOpenOption.APPEND);reject("activated panel tamper included in activation inventory",()->activeConfiguration(prepared.destination));check(configuration.equals(prepared.staging.resolve("config")),"Positive activation stayed in fresh reserved generation");check(!loaded.getMachine().isEnabled()&&!loaded.getMachine().isHomed(),"No enable/home on adopted tamper probe");check(!Arrays.equals(before,read(panelPath,MAX_FILE)),"Tampered fixture retained, not repaired/relaunched");
            } else {
                Path stage=input;JsonObject m=object(read(stage.resolve("manifest.json"),256*1024));String entry=NativePortablePanelLibrary.records(m).get(0).get("entry").getAsString();Path panelPath=stage.resolve(entry);
                if(mode.equals("missing-panel"))Files.delete(panelPath);else if(mode.equals("corrupt-panel"))Files.writeString(panelPath,"<openpnp-panel version=\"2.0\"><invalid",StandardCharsets.UTF_8);
                else if(mode.equals("native-fallback")){
                    JsonObject br=NativePortableBoardLibrary.panelBoardRecords(m).get(0);Path source=stage.resolve(br.get("entry").getAsString()),alias=panelPath.getParent().resolve(source.getFileName());Files.copy(source,alias);Document d=xml(read(panelPath,MAX_FILE));for(Element child:NativePortablePanelLibrary.childElements(d))child.setAttribute("file-name",stage.resolve("missing").resolve(source.getFileName()).toString());Files.write(panelPath,encode(d));
                }else throw new AssertionError("Unknown child mode "+mode);
                Configuration.initialize(stage.resolve("config").toFile());loaded=Configuration.get();final Configuration actual=loaded;try(var constraint=loaded.getScripting().constrainExecution(file->{throw new IllegalStateException("Scripts disabled during boundary proof");})){
                    loaded.load();if(mode.equals("native-fallback"))check(loaded.getBoards().size()==2&&loaded.getPanels().size()==1,"Native basename fallback creates extra canonical Board identity");else check(loaded.getPanels().isEmpty(),"Actual native loader silently omits missing/corrupt Panel");
                    reject("complete native library verification after "+mode,()->NativePortableLibraries.verifyLoaded(actual,m,stage));check(!Files.exists(stage.getParent().resolve("adoption.json")),"Manual native skip/fallback probe has no activation");check(!loaded.getMachine().isEnabled()&&!loaded.getMachine().isHomed(),"Native skip/fallback acquires no enable/home authority");
                }
            }
            System.out.println("OPENPNP_PORTABLE_PANEL_BOUNDARY_CHILD "+JSON.toJson(Bridge.map("mode",mode,"passed",true,"assertions",assertions,"refusals",refusals,"cases",cases,"native_enable_requested",false,"native_home_requested",false,"job_processor_called",false,"physical_qualification",false)));
        }finally{if(loaded!=null)loaded.getMachine().close();}
    }
    public static void main(String[] args)throws Exception {
        int exit=0;try {
            if(args.length==2){childMain(args[0],Path.of(args[1]));System.exit(0);return;}
            fixture();sourceCases();pseudoSourceCases();Path good=root.resolve("supported.zip");Map<String,Object> receipt=export(c,good);Map<String,byte[]> entries=unzip(read(good,MAX_BUNDLE));archiveCases(entries);xmlCases(entries);stagingCases(good,entries);
            check(enables==0&&homes==0&&headEvents==0&&!c.getMachine().isEnabled()&&!c.getMachine().isHomed(),"All cases have no native machine authority/effects");
            System.out.println("OPENPNP_PORTABLE_PANEL_LIBRARY_BOUNDARY_RESULT "+JSON.toJson(Bridge.map("passed",true,"assertions",assertions,"refusals",refusals,"cases",cases,"evidence_directory",root.toString(),"native_enable_events",enables,"native_home_events",homes,"native_head_events",headEvents,"physical_qualification",false,"job_processor_called",false,"class_origins",Bridge.map("Bridge",Bridge.class.getProtectionDomain().getCodeSource().getLocation().toString(),"NativePortableConfiguration",NativePortableConfiguration.class.getProtectionDomain().getCodeSource().getLocation().toString(),"Panel",Panel.class.getProtectionDomain().getCodeSource().getLocation().toString()))));
        }catch(Throwable failure){System.out.println("OPENPNP_PORTABLE_PANEL_LIBRARY_BOUNDARY_FAILURE "+JSON.toJson(Bridge.map("passed",false,"assertions_before_failure",assertions,"refusals_before_failure",refusals,"cases_before_failure",cases,"evidence_directory",root==null?null:root.toString())));failure.printStackTrace();exit=1;}finally{if(c!=null)c.getMachine().close();}System.exit(exit);
    }
}
