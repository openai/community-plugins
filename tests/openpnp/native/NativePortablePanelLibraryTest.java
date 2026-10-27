/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.lang.management.ManagementFactory;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.openpnp.model.*;
import org.openpnp.model.Abstract2DLocatable.Side;
import org.openpnp.machine.reference.feeder.ReferenceStripFeeder;
import org.openpnp.spi.Feeder;
import org.openpnp.util.Pair;
import org.openpnp.util.Utils2D;
import static org.openpnp.codex.NativePortableConfiguration.*;

/** Real fresh-JVM saved panel library transfer. No production overrides, controller or motion calls. */
public final class NativePortablePanelLibraryTest {
    private static int checks;
    private static final String PROFILE="saved-board-child-panel-library-v1";
    private static void check(boolean v,String s){checks++;if(!v)throw new AssertionError(s);}
    private static JsonObject obj(Object...v){return JSON.toJsonTree(Bridge.map(v)).getAsJsonObject();}
    private static void save(Path p,Object v)throws Exception{Files.writeString(p,JSON.toJson(v)+"\n",StandardOpenOption.CREATE_NEW);}
    private static Map<String,Object> pose(Location p){return Bridge.map("units",p.getUnits().name(),"x",p.getX(),"y",p.getY(),"z",p.getZ(),"rotation",p.getRotation());}
    private static Location mm(double x,double y,double z,double r){return new Location(LengthUnit.Millimeters,x,y,z,r);}
    private static void near(Location a,Location b,String label){Location x=a.convertToUnits(LengthUnit.Millimeters),y=b.convertToUnits(LengthUnit.Millimeters);check(Math.abs(x.getX()-y.getX())<1e-9&&Math.abs(x.getY()-y.getY())<1e-9&&Math.abs(x.getZ()-y.getZ())<1e-9&&Math.abs(Utils2D.normalizeAngle180(x.getRotation()-y.getRotation()))<1e-9,label);}
    private static Placement placement(Configuration c,String id,Placement.Type type,Side side,Location location){Placement p=new Placement(id);p.setPart(c.getPart("R0805-1K"));p.setType(type);p.setSide(side);p.setLocation(location);return p;}
    private static Board board(Configuration c,Path file,boolean empty)throws Exception{
        Board b=c.getBoard(file.toFile());b.setName(empty?"empty supported board":"identical board definition");b.setDimensions(empty?mm(15,10,0,0):new Location(LengthUnit.Inches,1,.75,0,0));
        if(!empty){b.addPlacement(placement(c,"R1",Placement.Type.Placement,Side.Top,mm(4,5,0,90)));b.addPlacement(placement(c,"FID",Placement.Type.Fiducial,Side.Bottom,new Location(LengthUnit.Inches,.2,.15,0,25)));}
        c.saveBoard(b);return b;
    }
    private static BoardLocation child(Board b,String id,Side side,Location location,boolean enabled,boolean fids){BoardLocation l=new BoardLocation(new Board(b));l.setId(id);l.setSide(side);l.setLocation(location);l.setLocallyEnabled(enabled);l.setCheckFiducials(fids);return l;}
    private static Panel panel(Configuration c,Path file,List<Board> b)throws Exception{
        Panel p=c.getPanel(file.toFile());p.setName("identical saved panel definition");p.setDimensions(new Location(LengthUnit.Inches,4,3,0,0));
        p.addChild(child(b.get(0),"A",Side.Top,mm(2,3,0,30),true,false));
        p.addChild(child(b.get(0),"B",Side.Bottom,new Location(LengthUnit.Inches,1.5,.75,.1,-45),false,true));
        p.addChild(child(b.get(1),"C",Side.Top,mm(50,3,0,-90),true,true));
        p.addChild(child(b.get(2),"EMPTY",Side.Bottom,mm(70,30,0,180),false,false));
        p.addPlacement(placement(c,"OWN",Placement.Type.Fiducial,Side.Top,new Location(LengthUnit.Inches,.1,.2,0,15)));
        p.addPseudoPlacement(p.createPseudoPlacement("A⇒FID"));p.addPseudoPlacement(p.createPseudoPlacement("B⇒FID"));c.savePanel(p);return p;
    }
    private static Map<String,Object> placementSnapshot(Placement p,Configuration c){
        Part part=p.getPart();check(part!=null&&c.getPart(part.getId())==part,"exact canonical Part identity");check(part.getPackage()!=null&&c.getPackage(part.getPackage().getId())==part.getPackage(),"exact canonical Package identity");
        return Bridge.map("id",p.getId(),"type",p.getType().name(),"side",p.getSide().name(),"location",pose(p.getLocation()),"part_id",part.getId(),"enabled",p.isEnabled(),"error_handling",p.getErrorHandling().name(),"comments",p.getComments());
    }
    private static JsonObject snapshot(Configuration c)throws Exception{
        check(c.getBoards().size()==3&&c.getPanels().size()==2,"exact canonical library cardinality");
        List<Map<String,Object>> boards=new ArrayList<>(),panels=new ArrayList<>();List<Board> bs=c.getBoards();Set<Path> paths=new HashSet<>();
        for(Board b:bs){check(b.getDefinition()==b,"self-defining canonical board");check(paths.add(b.getFile().toPath().toRealPath()),"board files distinct");check(!b.isDirty(),"saved board remains clean");List<Map<String,Object>> rows=new ArrayList<>();for(Placement p:b.getPlacements())rows.add(placementSnapshot(p,c));boards.add(Bridge.map("name",b.getName(),"dimensions",pose(b.getDimensions()),"placements",rows));}
        check(bs.get(0)!=bs.get(1)&&Arrays.equals(read(bs.get(0).getFile().toPath(),MAX_FILE),read(bs.get(1).getFile().toPath(),MAX_FILE)),"same bytes do not collapse distinct board definitions");
        List<Panel> ps=c.getPanels();check(ps.get(0)!=ps.get(1),"distinct native panel definitions");
        for(Panel p:ps){check(p.getDefinition()==p&&!p.isDirty(),"saved canonical panel identity/clean flag");check(paths.add(p.getFile().toPath().toRealPath()),"panel files distinct");check(p.getChildren().size()==4,"all children including empty/disabled counted");
            List<Map<String,Object>> children=new ArrayList<>(),own=new ArrayList<>(),pseudos=new ArrayList<>();int n=0;
            for(PlacementsHolderLocation<?> ph:p.getChildren()){check(ph.getClass()==BoardLocation.class,"only direct exact BoardLocation children");BoardLocation bl=(BoardLocation)ph;Board expected=bs.get(n==0||n==1?0:n-1);check(bl.getBoard()!=expected&&bl.getBoard().getDefinition()==expected,"child is a native instance of exact canonical board");check(bl.getBoard().getFile().getCanonicalFile().equals(expected.getFile().getCanonicalFile()),"child canonical file is exact board file");check(bl.getPlaced()==null||bl.getPlaced().isEmpty(),"no legacy placed map transfer");check(bl.getPlacementsTransformStatus()==PlacementsHolderLocation.PlacementsTransformStatus.NotSet,"registration remains NotSet");for(Placement pp:bl.getBoard().getPlacements())placementSnapshot(pp,c);children.add(Bridge.map("id",bl.getId(),"board_index",bs.indexOf(expected),"location",pose(bl.getLocation()),"side",bl.getSide().name(),"enabled",bl.isLocallyEnabled(),"check_fiducials",bl.isCheckFiducials()));n++;}
            check(p.getChildren().get(0).getPlacementsHolder().getDefinition()==p.getChildren().get(1).getPlacementsHolder().getDefinition(),"repeated child instances share canonical board definition");
            for(Placement pp:p.getPlacements())own.add(placementSnapshot(pp,c));check(own.size()==1&&p.getPlacements().get(0).getType()==Placement.Type.Fiducial,"own panel fiducial retained");
            check(p.getPseudoPlacements().size()==2&&p.getPseudoPlacementIds().equals(Arrays.asList("A⇒FID","B⇒FID")),"exact saved pseudo IDs/order retained");
            for(Placement pseudo:p.getPseudoPlacements()){
                String childId=pseudo.getId().substring(0,pseudo.getId().indexOf('⇒'));BoardLocation selected=(BoardLocation)p.getChildren().stream().filter(x->x.getId().equals(childId)).findFirst().orElseThrow();Placement leaf=selected.getBoard().getPlacements().get("FID");Pair<List<PlacementsHolderLocation<?>>,Placement> resolved=p.getDescendantPlacement(pseudo.getId());
                check(resolved!=null&&resolved.first.size()==1&&resolved.first.get(0)==selected&&resolved.second==leaf,"native pseudo resolves exact branch and leaf objects");
                Location expected=Utils2D.calculateBoardPlacementLocation(selected,leaf).derive(null,null,0.0,null);if(selected.getGlobalSide()!=leaf.getSide())expected=expected.derive(null,null,null,Utils2D.normalizeAngle180(-expected.getRotation()+2*leaf.getLocation().getRotation()));near(pseudo.getLocation(),expected,"independent native transform oracle for pseudo location");check(pseudo.getSide()==leaf.getSide().flip(selected.getGlobalSide()==Side.Bottom),"native pseudo Bottom reflection side");check(pseudo.getPart()==leaf.getPart()&&pseudo.getType()==leaf.getType(),"pseudo retains exact native target Part/type");pseudos.add(placementSnapshot(pseudo,c));
            }
            panels.add(Bridge.map("name",p.getName(),"dimensions",pose(p.getDimensions()),"children",children,"own",own,"pseudos",pseudos));
        }
        check(Arrays.equals(read(ps.get(0).getFile().toPath(),MAX_FILE),read(ps.get(1).getFile().toPath(),MAX_FILE)),"byte-identical panel files remain distinct identities");
        check(ps.get(0).getChild(0).getPlacementsHolder().getDefinition()==ps.get(1).getChild(0).getPlacementsHolder().getDefinition(),"two panels share same canonical native board");
        return obj("boards",boards,"panels",panels,"native_feed_counts",feedCounts(c));
    }
    private static Map<String,Integer> feedCounts(Configuration c){Map<String,Integer> m=new TreeMap<>();for(Feeder f:c.getMachine().getFeeders())if(f instanceof ReferenceStripFeeder)m.put(f.getId(),((ReferenceStripFeeder)f).getFeedCount());else if(f instanceof org.openpnp.machine.reference.feeder.ReferenceTrayFeeder)m.put(f.getId(),((org.openpnp.machine.reference.feeder.ReferenceTrayFeeder)f).getFeedCount());return m;}
    private static Map<String,String> libraryFiles(Configuration c)throws Exception{Map<String,String> m=new TreeMap<>();for(Board b:c.getBoards())m.put(b.getFile().toString(),sha(read(b.getFile().toPath(),MAX_FILE)));for(Panel p:c.getPanels())m.put(p.getFile().toString(),sha(read(p.getFile().toPath(),MAX_FILE)));return m;}
    private static Path token(Path root,String name)throws Exception {Path p=Files.createFile(root.resolve(name),java.nio.file.attribute.PosixFilePermissions.asFileAttribute(java.nio.file.attribute.PosixFilePermissions.fromString("rw-------")));Files.writeString(p,(UUID.randomUUID().toString()+UUID.randomUUID().toString()).replace("-",""),StandardOpenOption.WRITE);return p;}
    private static Map<String,Object> call(Bridge b,String method,Object...fields)throws Exception{return (Map<String,Object>)b.call("openpnp_"+method,obj(fields));}
    private static void manifest(Map<String,Object> exported,int version)throws Exception{Map<?,?> m=(Map<?,?>)exported.get("manifest");check(((Number)m.get("version")).intValue()==version,"explicit version "+version);check(Boolean.FALSE.equals(m.get("operational_journal_included"))&&Boolean.FALSE.equals(m.get("physical_state_transferred"))&&Boolean.FALSE.equals(m.get("scripts_activated")),"no operational/physical/script authority");if(version==3){Map<?,?> p=(Map<?,?>)m.get("panel_library");check(PROFILE.equals(p.get("profile")),"closed panel profile");check(((Number)p.get("panel_count")).intValue()==2&&((Number)p.get("child_count")).intValue()==8,"exact panel/child multiplicity");check(((Number)p.get("placement_count")).intValue()==2&&((Number)p.get("pseudo_count")).intValue()==4,"own and pseudo multiplicity");check(((Number)((Map<?,?>)m.get("board_library")).get("board_count")).intValue()==3,"complete three-board inventory");}}
    /** Read-only reflection observes native PropertyChangeSupport; no listener/model override. */
    private static final class ListenerBinding {
        final String property;final java.beans.PropertyChangeListener listener;
        ListenerBinding(java.beans.PropertyChangeListener p){if(p instanceof java.beans.PropertyChangeListenerProxy){java.beans.PropertyChangeListenerProxy proxy=(java.beans.PropertyChangeListenerProxy)p;property=proxy.getPropertyName();listener=proxy.getListener();}else{property=null;listener=p;}}
        boolean same(ListenerBinding b){return Objects.equals(property,b.property)&&listener==b.listener;}
    }
    private static List<ListenerBinding> listeners(AbstractModelObject object)throws Exception {
        java.lang.reflect.Field field=AbstractModelObject.class.getDeclaredField("propertyChangeSupport");field.setAccessible(true);java.beans.PropertyChangeSupport support=(java.beans.PropertyChangeSupport)field.get(object);List<ListenerBinding> values=new ArrayList<>();for(java.beans.PropertyChangeListener listener:support.getPropertyChangeListeners())values.add(new ListenerBinding(listener));return values;
    }
    private static void original(IdentityHashMap<AbstractModelObject,List<ListenerBinding>> all,AbstractModelObject object)throws Exception {if(!all.containsKey(object))all.put(object,listeners(object));}
    private static void holderListeners(IdentityHashMap<AbstractModelObject,List<ListenerBinding>> all,PlacementsHolder<?> holder)throws Exception {
        original(all,holder);for(Placement p:holder.getPlacements())original(all,p);
        if(holder instanceof Panel){Panel panel=(Panel)holder;for(Placement p:panel.getPseudoPlacements())original(all,p);for(PlacementsHolderLocation<?> child:panel.getChildren()){original(all,child);holderListeners(all,child.getPlacementsHolder());}}
    }
    private static IdentityHashMap<AbstractModelObject,List<ListenerBinding>> listenerSnapshot(Configuration c)throws Exception {
        IdentityHashMap<AbstractModelObject,List<ListenerBinding>> all=new IdentityHashMap<>();for(Board board:c.getBoards())holderListeners(all,board);for(Panel panel:c.getPanels())holderListeners(all,panel);for(Part part:c.getParts())original(all,part);for(org.openpnp.model.Package pkg:c.getPackages())original(all,pkg);return all;
    }
    private static int sameListeners(IdentityHashMap<AbstractModelObject,List<ListenerBinding>> before)throws Exception {
        int total=0;for(Map.Entry<AbstractModelObject,List<ListenerBinding>> row:before.entrySet()){
            List<ListenerBinding> after=listeners(row.getKey());check(after.size()==row.getValue().size(),"native listener count unchanged on "+row.getKey().getClass().getSimpleName());boolean[] used=new boolean[after.size()];
            for(ListenerBinding expected:row.getValue()){boolean found=false;for(int i=0;i<after.size();i++)if(!used[i]&&expected.same(after.get(i))){used[i]=true;found=true;break;}check(found,"exact property/target listener identity and multiplicity retained");total++;}
        }return total;
    }
    private static final List<Map<String,Object>> lifecycleObservations=new ArrayList<>();
    private static void repeatedLifecycle(Configuration c,Path root,JsonObject originalSnapshot,Map<String,String> originalFiles)throws Exception {
        for(int i=0;i<2;i++){
            IdentityHashMap<AbstractModelObject,List<ListenerBinding>> before=listenerSnapshot(c);Map<String,Object> result=export(c,root.resolve("repeat-export-"+i+".zip"));manifest(result,3);int count=sameListeners(before);
            check(originalSnapshot.equals(snapshot(c))&&originalFiles.equals(libraryFiles(c)),"repeated export preserves complete original model and source bytes");lifecycleObservations.add(Bridge.map("kind","valid-export","index",i,"observed_objects",before.size(),"listener_bindings",count,"exact_listener_multisets_preserved",true));
        }
        // A dirty=false second-panel name mismatch passes native/XML shape checks and reaches
        // detached saved-model comparison. Each failed full export must dispose its comparisons.
        Panel changed=c.getPanels().get(1);String name=changed.getName();boolean dirty=changed.isDirty();
        try {
            changed.setName(name+" deliberately unsaved");changed.setDirty(false);
            for(int i=0;i<2;i++){
                IdentityHashMap<AbstractModelObject,List<ListenerBinding>> before=listenerSnapshot(c);Path output=root.resolve("refused-export-"+i+".zip");boolean refused=false;
                try{export(c,output);}catch(Bridge.Fault fault){refused=true;check("LIBRARY_CHANGED".equals(fault.code),"late saved/current mismatch keeps original typed refusal");}
                check(refused&&!Files.exists(output),"repeated failed comparison publishes no archive");int count=sameListeners(before);check(originalFiles.equals(libraryFiles(c)),"refusal retains source files");check(changed.getName().equals(name+" deliberately unsaved")&&!changed.isDirty(),"refusal does not repair or overwrite caller model");
                lifecycleObservations.add(Bridge.map("kind","late-refusal","index",i,"code","LIBRARY_CHANGED","observed_objects",before.size(),"listener_bindings",count,"exact_listener_multisets_preserved",true));
            }
        }finally{changed.setName(name);changed.setDirty(dirty);}
        check(originalSnapshot.equals(snapshot(c)),"test fixture restoration retains original native geometry/identities");IdentityHashMap<AbstractModelObject,List<ListenerBinding>> before=listenerSnapshot(c);manifest(export(c,root.resolve("after-refusals.zip")),3);int count=sameListeners(before);check(originalFiles.equals(libraryFiles(c)),"successful export after refused captures preserves bytes");
        lifecycleObservations.add(Bridge.map("kind","valid-after-refusals","observed_objects",before.size(),"listener_bindings",count,"exact_listener_multisets_preserved",true));
    }
    private static void phase(String mode,Path root,Path samples)throws Exception{
        Configuration c=null;NativePortableLaunch launch=null;Bridge bridge=null;int exit=0;Throwable error=null;
        try{
            if(mode.equals("source")){
                Path cfg=root.resolve("source-config");Configuration.initialize(cfg.toFile());c=Configuration.get();c.load();SimulatorMain.accelerateFixture(c);SimulatorMain.settleFreshFixture(cfg);c=Configuration.get();
                manifest(export(c,root.resolve("legacy-v1.zip")),1);
                List<Board> boards=Arrays.asList(board(c,root.resolve("one.board.xml"),false),board(c,root.resolve("two.board.xml"),false),board(c,root.resolve("empty.board.xml"),true));c.save();manifest(export(c,root.resolve("legacy-v2.zip")),2);
                panel(c,root.resolve("one.panel.xml"),boards);panel(c,root.resolve("two.panel.xml"),boards);for(Feeder f:c.getMachine().getFeeders())if(f instanceof ReferenceStripFeeder){((ReferenceStripFeeder)f).setFeedCount(3);break;}c.save();
                JsonObject before=snapshot(c);save(root.resolve("source-observation.json"),before);Map<String,String> bytes=libraryFiles(c);IdentityHashMap<AbstractModelObject,List<ListenerBinding>> originalListeners=listenerSnapshot(c);Map<String,Object> out=export(c,root.resolve("panels.zip"));manifest(out,3);sameListeners(originalListeners);save(root.resolve("source-export.json"),out);check(bytes.equals(libraryFiles(c)),"export leaves all original library files byte-exact");check(before.equals(snapshot(c)),"export leaves source native geometry/identities/counts exact");repeatedLifecycle(c,root,before,bytes);
                bridge=new Bridge(c,token(root,"source-token"),root.resolve("source-journal"),samples,0,true);save(root.resolve("source-machine.json"),call(bridge,"get_status").get("machine_id"));
            }else if(mode.equals("adopt")){
                Path zip=root.resolve("panels.zip");Prepared prepared=prepare(zip,sha(read(zip,MAX_BUNDLE)),root.resolve("adopted"));Map<String,Object> out=validateAndPublish(prepared);check(Boolean.TRUE.equals(out.get("adopted"))&&Boolean.TRUE.equals(out.get("native_xml_roundtrip_verified")),"dedicated native validation and publication succeeds");c=Configuration.get();check(object(read(root.resolve("source-observation.json"),MAX_FILE)).equals(snapshot(c)),"native validation save/load preserves exact supported library semantics");check(activeConfiguration(prepared.destination).equals(prepared.staging.resolve("config")),"complete activation gate");save(root.resolve("adoption-result.json"),out);
            }else if(mode.equals("launch")){
                launch=NativePortableLaunch.claim(root.resolve("adopted"),root.resolve("target-journal"));c=launch.initialize();check(object(read(root.resolve("source-observation.json"),MAX_FILE)).equals(snapshot(c)),"fresh first launch retains canonical model fields and source counters");
                try(var files=Files.list(launch.journalDirectory)){check(files.findAny().isEmpty(),"first launch starts without source operation journal");}
                bridge=new Bridge(c,token(root,"target-token"),launch.journalDirectory,samples,0,true,"adopted-simulator",null,launch);Map<String,Object> status=call(bridge,"get_status");check(!new JsonParser().parse(Files.readString(root.resolve("source-machine.json"))).getAsString().equals(status.get("machine_id")),"fresh native machine identity");check(status.get("job_id")==null&&"absent".equals(((Map<?,?>)status.get("job_progress")).get("state")),"no source job/lineage authority");Map<String,Object> loads=call(bridge,"get_board_loads");check(((List<?>)loads.get("loads")).isEmpty()&&((List<?>)loads.get("roots")).isEmpty(),"new empty native board load inventory");
                Map<String,String> bytes=libraryFiles(c);Map<String,Object> reexport=export(c,root.resolve("reexport.zip"));manifest(reexport,3);check(bytes.equals(libraryFiles(c)),"re-export cannot modify adopted saved definitions");save(root.resolve("target-observation.json"),snapshot(c));save(root.resolve("reexport-result.json"),reexport);
                // Public native Job APIs use the adopted canonical Panel; transfer itself does not execute it.
                Job job=new Job();PanelLocation location=new PanelLocation(new Panel(c.getPanels().get(0)));location.setId("ADOPTED-PANEL");location.setLocation(mm(100,100,0,0));location.setCheckFiducials(false);job.addBoardOrPanelLocation(location);PanelLocation.setParentsOfAllDescendants(job.getRootPanelLocation());Path jf=root.resolve("adopted-panel.job.xml");c.saveJob(job,jf.toFile());Job loaded=c.loadJob(jf.toFile());check(loaded.getBoardLocations().size()==4,"native Job save/load retains all adopted direct children including empty");check(loaded.getPlacedStatusSnapshot().isEmpty(),"native Job starts with no placed history");for(BoardLocation b:loaded.getBoardLocations())check(c.getBoards().contains(b.getBoard().getDefinition()),"native Job board links canonical adopted registry");check(((PanelLocation)loaded.getRootPanelLocation().getChildren().get(0)).getPanel().getDefinition()==c.getPanels().get(0),"native Job panel links exact adopted registry");check(bytes.equals(libraryFiles(c)),"native Job save/load leaves saved definitions unchanged");
            }else if(mode.equals("repeat")){
                try{NativePortableLaunch.claim(root.resolve("adopted"),root.resolve("forbidden-repeat-journal"));throw new AssertionError("Consumed launch replayed");}catch(Bridge.Fault f){check("ADOPTION_ALREADY_LAUNCHED".equals(f.code),"repeat has explicit spent disposition");}check(!Files.exists(root.resolve("forbidden-repeat-journal")),"repeat performs no new journal reservation");check(Files.exists(root.resolve("adopted/first-launch-closed.json")),"owned launch close retained");
            }else throw new IllegalArgumentException(mode);
            if(c!=null)check(!c.getMachine().isEnabled()&&!c.getMachine().isHomed(),"no native enable/home authority acquired");
        }catch(Throwable t){error=t;exit=1;t.printStackTrace();}
        finally{try{if(bridge!=null)bridge.close();if(launch!=null)launch.close();else if(c!=null)c.getMachine().close();}catch(Throwable cleanup){if(error!=null)error.addSuppressed(cleanup);else{error=cleanup;exit=1;}cleanup.printStackTrace();}JsonObject result=obj("passed",exit==0,"phase",mode,"checks",checks,"listener_lifecycle",lifecycleObservations,"error",error==null?null:error.toString(),"test_invokes_machine_enable_or_home",false,"test_invokes_job_processor",false,"native_effect_collector_installed",false,"observed_native_placement_count",null,"source_counter_scope","three configured counter units are fixture data; no feed or reset is inferred from transfer","physical_qualification",false,"class_origins",Bridge.map("Bridge",Bridge.class.getProtectionDomain().getCodeSource().getLocation().toString(),"Panel",Panel.class.getProtectionDomain().getCodeSource().getLocation().toString()));save(root.resolve(mode+"-result.json"),result);System.out.println("OPENPNP_PORTABLE_PANEL_LIBRARY_PHASE "+JSON.toJson(result));}
        System.exit(exit);
    }
    public static void main(String[] args)throws Exception{
        if(args.length==3){phase(args[0],Path.of(args[1]),Path.of(args[2]));return;}if(args.length!=1)throw new IllegalArgumentException("Expected native SAMPLE_ROOT");
        Path root=Files.createTempDirectory("native-portable-panel-library-");List<Map<String,Object>> rows=new ArrayList<>();boolean passed=true;int total=0;
        for(String phase:List.of("source","adopt","launch","repeat")){
            List<String> command=new ArrayList<>();command.add(Path.of(System.getProperty("java.home"),"bin/java").toString());command.addAll(ManagementFactory.getRuntimeMXBean().getInputArguments());command.addAll(List.of("-cp",System.getProperty("java.class.path"),NativePortablePanelLibraryTest.class.getName(),phase,root.toString(),Path.of(args[0]).toAbsolutePath().toString()));Process child=null;boolean forced=false;String error=null;int exit=-1;Path log=root.resolve(phase+".log");
            try{ProcessBuilder b=new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(log.toFile());b.environment().keySet().removeIf(k->{String u=k.toUpperCase(Locale.ROOT);return Set.of("JAVA_TOOL_OPTIONS","JDK_JAVA_OPTIONS","_JAVA_OPTIONS","CLASSPATH").contains(u)||u.startsWith("LD_")||u.startsWith("DYLD_");});child=b.start();if(!child.waitFor(90,TimeUnit.SECONDS)){forced=true;child.destroyForcibly();check(child.waitFor(10,TimeUnit.SECONDS),"owned watchdog child reaped");}exit=child.exitValue();}catch(Throwable t){error=t.toString();}finally{if(child!=null&&child.isAlive()){forced=true;child.destroyForcibly();child.waitFor(10,TimeUnit.SECONDS);}}
            JsonObject result=Files.exists(root.resolve(phase+"-result.json"))?object(read(root.resolve(phase+"-result.json"),MAX_FILE)):null;boolean ok=error==null&&!forced&&child!=null&&!child.isAlive()&&exit==0&&result!=null&&result.get("passed").getAsBoolean();if(ok)total+=result.get("checks").getAsInt();rows.add(Bridge.map("phase",phase,"passed",ok,"exit_code",exit,"pid",child==null?null:child.pid(),"owned_child_reaped",child!=null&&!child.isAlive(),"forced_cleanup",forced,"log_sha256",Files.exists(log)?sha(read(log,MAX_FILE)):null,"result",result,"error",error));if(!ok){passed=false;break;}
        }
        JsonObject receipt=obj("passed",passed&&rows.size()==4,"phases",rows,"phase_count",rows.size(),"assertions",total,"evidence_directory",root.toString(),"test_invokes_job_processor",false,"native_effect_collector_installed",false,"observed_native_placement_count",null,"physical_qualification",false,"production_override",false);save(root.resolve("receipt.json"),receipt);System.out.println("OPENPNP_PORTABLE_PANEL_LIBRARY_RESULT "+JSON.toJson(receipt));if(!receipt.get("passed").getAsBoolean())throw new AssertionError("Retained panel library failure at "+root);
    }
}
