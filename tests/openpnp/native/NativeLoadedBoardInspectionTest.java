/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.math.BigDecimal;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.model.Abstract2DLocatable.Side;

/** Real pinned native model test. Placed flags and observations are explicit synthetic
 * simulator fixtures; this test performs no job processing, measurement or physical inspection. */
public final class NativeLoadedBoardInspectionTest {
    static final Gson JSON=new Gson();static final List<String> passed=new ArrayList<>();
    static Configuration config;static Part part;static int assertions;
    interface Action {void run()throws Exception;}
    static void check(boolean value,String message){assertions++;if(!value)throw new AssertionError(message);}
    static void expect(String code,Action call)throws Exception {try{call.run();throw new AssertionError("Expected "+code);}catch(NativeLoadedBoardInspection.Fault e){check(code.equals(e.code),"Expected "+code+", got "+e.code+": "+e.getMessage());}}
    static JsonObject object(Object... fields){return JSON.toJsonTree(Bridge.map(fields)).getAsJsonObject();}
    static final class Fixture {
        final Job job;final NativeBoardLoads loads;final Map<String,Object> scope,context;final BoardLocation a,b;final String loadedId;
        Fixture(int count,boolean placed)throws Exception {this(count,placed,false);}
        Fixture(int count,boolean placed,boolean pads)throws Exception {job=CanonicalJobImporter.load(config,canonical(count));a=job.getBoardLocations().get(0);b=job.getBoardLocations().get(1);
            if(pads){Board definition=a.getBoard().getDefinition();Pad.Circle circle=new Pad.Circle();circle.setRadius(0.5);Pad.Ellipse ellipse=new Pad.Ellipse();ellipse.setWidth(1);ellipse.setHeight(0.5);Pad.RoundRectangle rectangle=new Pad.RoundRectangle();rectangle.setWidth(1.5);rectangle.setHeight(0.7);rectangle.setRoundness(0.2);for(Pad shape:List.of(circle,ellipse,rectangle))definition.addSolderPastePad(new BoardPad(shape,new Location(LengthUnit.Millimeters,1,2,0,0)));for(BoardLocation board:job.getBoardLocations())board.setBoard(new Board(definition));}
            loads=new NativeBoardLoads((type,payload)->{});loads.bindJob(job,"inspection-test-job",true);
            if(placed)for(BoardLocation board:job.getBoardLocations())for(Placement p:board.getBoard().getPlacements())if(p.isEnabled()&&p.getType()==Placement.Type.Placement&&p.getSide()==board.getGlobalSide())job.storePlacedStatus(board,p.getId(),true);
            loads.checkpoint(job);scope=loads.ledgerScope(job);
            loadedId=(String)((Map<?,?>)((List<?>)scope.get("boards")).get(0)).get("loaded_board_id");
            context=Bridge.map("bridge_instance_id","inspection-model-instance","job_id","inspection-test-job","job_revision",loads.jobRevision(),"config_revision","cfg-1","board_load_revision",loads.revision(),"lineage_id","inspection-test-lineage","lineage_revision",1,"ownership_epoch",1,"job_state","completed");}
        NativeLoadedBoardInspection.Snapshot capture()throws Exception{return NativeLoadedBoardInspection.capture(config,job,scope,context,loadedId);}
    }
    static JsonObject canonical(int count){JsonObject job=object("schemaVersion",1,"id","native-inspection-fixture","units","mm","coordinateConvention","openpnp-top-view");
        JsonArray parts=new JsonArray();parts.add(object("id",part.getId(),"packageId",part.getPackage().getId(),"heightMm",part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue()));job.add("parts",parts);
        JsonObject board=object("id","shared","widthMm",100,"heightMm",100);JsonArray placements=new JsonArray();
        for(int i=0;i<count;i++)placements.add(placement("R"+(i+1),"top","placement",true));placements.add(placement("BOTTOM","bottom","placement",true));placements.add(placement("DISABLED","top","placement",false));placements.add(placement("FID","top","fiducial",true));board.add("placements",placements);JsonArray boards=new JsonArray();boards.add(board);job.add("boards",boards);
        JsonArray children=new JsonArray();children.add(instance("A","board","shared",0));children.add(instance("B","board","shared",120));JsonObject panel=object("id","shared-panel","widthMm",250,"heightMm",100);panel.add("children",children);JsonArray panels=new JsonArray();panels.add(panel);job.add("panels",panels);
        JsonArray roots=new JsonArray();roots.add(instance("P","panel","shared-panel",0));job.add("instances",roots);return job;}
    static JsonObject placement(String ref,String side,String type,boolean enabled){return object("ref",ref,"partId",part.getId(),"packageId",part.getPackage().getId(),"heightMm",part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue(),"x",5,"y",5,"z",0,"rotation",0,"side",side,"type",type,"enabled",enabled);}
    static JsonObject instance(String id,String kind,String definition,int x){return object("id",id,"kind",kind,"definitionId",definition,"x",x,"y",0,"z",0,"rotation",0,"side","top","enabled",true);}
    static JsonObject submission(NativeLoadedBoardInspection.Snapshot snapshot){JsonArray records=new JsonArray();for(Object raw:(List<?>)snapshot.preview().get("required_placements")){Map<?,?> p=(Map<?,?>)raw;records.add(object("holder_instance_id",p.get("holder_instance_id"),"placement_id",p.get("placement_id"),"presence","present","polarity","not_applicable","dx_mm",0.03,"dy_mm",0.04,"rotation_deg",1,"xy_uncertainty_mm",0.01,"rotation_uncertainty_deg",0.2));}
        JsonObject result=object("operator_label","Model-test operator","operator_note","Synthetic local observations for a native simulator model test; no instrument reading.","tolerances",object("xy_mm",0.1,"rotation_deg",2),"records",records,"artifact_refs",new JsonArray());return result;}
    static JsonObject copy(JsonElement value){return new JsonParser().parse(value.toString()).getAsJsonObject();}
    static void replace(JsonObject submission,int index,JsonObject value){JsonArray output=new JsonArray();int n=0;for(JsonElement row:submission.getAsJsonArray("records")){if(n++==index){if(value!=null)output.add(value);}else output.add(row);}submission.add("records",output);}
    static JsonObject row(JsonObject submission,int index){return submission.getAsJsonArray("records").get(index).getAsJsonObject();}
    static int count(Map<String,Object> result,String key){return ((Number)result.get(key)).intValue();}
    static Map<String,Object> evaluate(NativeLoadedBoardInspection.Snapshot snapshot,JsonObject input)throws Exception{return snapshot.evaluate(input);}
    static void outcome(NativeLoadedBoardInspection.Snapshot snapshot,JsonObject input,String expected)throws Exception {check(expected.equals(evaluate(snapshot,input).get("outcome")),"Expected observation outcome "+expected);}
    public static void main(String[] args)throws Exception {Path root=Files.createTempDirectory("openpnp-loaded-board-inspection-");Configuration.initialize(root.toFile());config=Configuration.get();config.load();part=config.getPart("R0603-1K");int exit=0;
        try{captureAndCoverage();classification();staleness();invalidInputs();limits();readOnlyPads();padRefusals();outputBounds();check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"Inspection model tests never enabled or homed the native machine");
            System.out.println("OPENPNP_NATIVE_LOADED_BOARD_INSPECTION_RESULT "+JSON.toJson(Bridge.map("passed",passed,"assertions",assertions,"profile",NativeLoadedBoardInspection.PROFILE,"simulation_only",true,"synthetic_observations",true,"native_placed_flags_set_by_fixture",true,"native_job_processor_called",false,"physical_inspection_performed",false,"hardware_qualified",false)));}
        catch(Throwable e){e.printStackTrace();exit=1;}finally{config.getMachine().close();}System.exit(exit);}
    static void captureAndCoverage()throws Exception {
        Fixture pending=new Fixture(2,false);expect("INSPECTION_NOT_COMPLETE",pending::capture);
        Fixture f=new Fixture(2,true);Map<String,Boolean> history=new TreeMap<>(f.job.getPlacedStatusSnapshot());boolean dirty=f.job.isDirty();NativeLoadedBoardInspection.Snapshot snapshot=f.capture();
        check(count(snapshot.preview(),"required_count")==2,"Only two ordinary enabled active-side placements are required");
        check(f.a.getBoard().getDefinition()==f.b.getBoard().getDefinition(),"Fixture uses two actual repeated instances of one native board definition");
        check(!((Map<?,?>)((List<?>)f.scope.get("boards")).get(0)).get("loaded_board_id").equals(((Map<?,?>)((List<?>)f.scope.get("boards")).get(1)).get("loaded_board_id")),"Repeated instances have distinct trusted loaded board IDs");
        for(Object raw:(List<?>)snapshot.preview().get("required_placements"))check(((Map<?,?>)raw).get("holder_instance_id").equals(f.a.getUniqueId()),"Selected scope contains only the exact first native board instance");
        outcome(snapshot,submission(snapshot),"passed");snapshot.revalidate(config,f.job,f.scope,f.context,f.loadedId);
        check(history.equals(f.job.getPlacedStatusSnapshot())&&dirty==f.job.isDirty(),"Capture/evaluation/revalidation preserve all opaque native placed history and dirty state");
        try{snapshot.preview().put("required_count",0);throw new AssertionError("mutable preview");}catch(UnsupportedOperationException expected){check(true,"Top-level preview immutable");}
        try{((Map<String,Object>)((List<?>)snapshot.preview().get("required_placements")).get(0)).put("native_placed",false);throw new AssertionError("mutable nested preview");}catch(UnsupportedOperationException expected){check(true,"Nested preview immutable");}
        JsonObject cross=submission(snapshot);row(cross,0).addProperty("holder_instance_id",f.b.getUniqueId());expect("INSPECTION_COVERAGE",()->snapshot.evaluate(cross));
        JsonObject duplicate=submission(snapshot);row(duplicate,1).addProperty("placement_id","R1");expect("INSPECTION_COVERAGE",()->snapshot.evaluate(duplicate));
        JsonObject missing=submission(snapshot);replace(missing,1,null);expect("INSPECTION_COVERAGE",()->snapshot.evaluate(missing));
        JsonObject extra=submission(snapshot);extra.getAsJsonArray("records").add(copy(row(extra,0)));expect("INSPECTION_COVERAGE",()->snapshot.evaluate(extra));
        passed.add("native completed-status gating; exact repeated-board selection; active-side/disabled/fiducial filtering; immutable read-only snapshots; exact coverage");
    }
    static void classification()throws Exception {
        NativeLoadedBoardInspection.Snapshot snapshot=new Fixture(2,true).capture();
        JsonObject within=submission(snapshot);row(within,0).addProperty("dx_mm",0.06);row(within,0).addProperty("dy_mm",0.08);row(within,0).addProperty("xy_uncertainty_mm",0);row(within,0).addProperty("rotation_deg",2);row(within,0).addProperty("rotation_uncertainty_deg",0);outcome(snapshot,within,"passed");
        JsonObject marginal=copy(within);row(marginal,0).addProperty("dx_mm",new BigDecimal("0.06000000000000000000000001"));outcome(snapshot,marginal,"failed");check(((Map<?,?>)((Map<?,?>)((List<?>)snapshot.evaluate(marginal).get("records")).get(0)).get("measurements")).get("dx_mm").equals(new BigDecimal("0.06000000000000000000000001")),"Receipt retains exact decimal residual that explains marginal failure");
        JsonObject crossing=submission(snapshot);row(crossing,0).addProperty("dx_mm",0.1);row(crossing,0).addProperty("dy_mm",0);outcome(snapshot,crossing,"uncertain");
        JsonObject clearFailure=submission(snapshot);row(clearFailure,0).addProperty("dx_mm",0.12);row(clearFailure,0).addProperty("dy_mm",0);outcome(snapshot,clearFailure,"failed");
        JsonObject wrap=submission(snapshot);row(wrap,0).addProperty("rotation_deg",359);outcome(snapshot,wrap,"passed");
        JsonObject rotationCross=submission(snapshot);row(rotationCross,0).addProperty("rotation_deg",2);outcome(snapshot,rotationCross,"uncertain");
        JsonObject rotationFail=submission(snapshot);row(rotationFail,0).addProperty("rotation_deg",3);outcome(snapshot,rotationFail,"failed");
        JsonObject incorrect=submission(snapshot);row(incorrect,0).addProperty("polarity","incorrect");outcome(snapshot,incorrect,"failed");
        JsonObject unknownPolarity=submission(snapshot);row(unknownPolarity,0).addProperty("polarity","unknown");outcome(snapshot,unknownPolarity,"uncertain");
        JsonObject mixed=submission(snapshot);replace(mixed,0,object("holder_instance_id",snapshot.preview().get("holder_instance_id"),"placement_id","R1","presence","missing","polarity","unknown"));replace(mixed,1,object("holder_instance_id",snapshot.preview().get("holder_instance_id"),"placement_id","R2","presence","unknown","polarity","unknown"));
        Map<String,Object> result=evaluate(snapshot,mixed);check(result.get("outcome").equals("failed")&&count(result,"failed_count")==1&&count(result,"uncertain_count")==1,"Failed and uncertain records remain separately countable");
        Map<?,?> first=(Map<?,?>)((List<?>)result.get("records")).get(0);check(((Map<?,?>)first.get("measurements")).isEmpty()&&!((List<?>)first.get("uncertainties")).isEmpty(),"Missing record has no fabricated measurement and retains polarity uncertainty");
        check(Boolean.FALSE.equals(result.get("production_authority_granted"))&&Boolean.FALSE.equals(result.get("hardware_qualified"))&&Boolean.FALSE.equals(result.get("instrument_authenticity_verified")),"Even passing or failed observations cannot grant physical or production authority");
        passed.add("exact decimal tolerance boundaries; Euclidean XY uncertainty; wrapped rotation; missing/unknown/polarity handling; mixed failed/uncertain outcomes without authority");
    }
    static void staleness()throws Exception {
        Fixture changed=new Fixture(2,true);NativeLoadedBoardInspection.Snapshot snap=changed.capture();changed.context.put("ownership_epoch",2);expect("INSPECTION_STALE",()->snap.revalidate(config,changed.job,changed.scope,changed.context,changed.loadedId));
        check(((Number)((Map<?,?>)snap.preview().get("context")).get("ownership_epoch")).intValue()==1,"Captured context does not alias mutable caller map");
        Fixture history=new Fixture(2,true);NativeLoadedBoardInspection.Snapshot old=history.capture();BoardLocation orphan=new BoardLocation();orphan.setId("removed");history.job.storePlacedStatus(orphan,"orphan-false",false);expect("INSPECTION_STALE",()->old.revalidate(config,history.job,history.scope,history.context,history.loadedId));
        Fixture nativeChanged=new Fixture(2,true);NativeLoadedBoardInspection.Snapshot pre=nativeChanged.capture();nativeChanged.b.setLocation(new Location(LengthUnit.Millimeters,119,0,0,0));expect("INSPECTION_STALE",()->pre.revalidate(config,nativeChanged.job,nativeChanged.scope,nativeChanged.context,nativeChanged.loadedId));
        Fixture identity=new Fixture(2,true);NativeLoadedBoardInspection.Snapshot bound=identity.capture();String before=(String)NativePlacementEdits.inspect(config,identity.job,0,200).get("source_fingerprint");BoardLocation replacement=new BoardLocation(identity.a);replacement.setParent(identity.a.getParent());((PanelLocation)identity.a.getParent()).getPanel().getChildren().set(0,replacement);String after=(String)NativePlacementEdits.inspect(config,identity.job,0,200).get("source_fingerprint");check(before.equals(after)&&replacement!=identity.a,"Actual native same-content child substitution retains semantic graph fingerprint");expect("INSPECTION_STALE",()->bound.revalidate(config,identity.job,identity.scope,identity.context,identity.loadedId));
        Fixture library=new Fixture(2,true);NativeLoadedBoardInspection.Snapshot registered=library.capture();String name=part.getName();try{part.setName("Inspection test library drift");expect("INSPECTION_STALE",()->registered.revalidate(config,library.job,library.scope,library.context,library.loadedId));}finally{part.setName(name);}
        Fixture load=new Fixture(2,true);NativeLoadedBoardInspection.Snapshot loadBound=load.capture();Map<String,Object> changedScope=NativeJournalJson.copy(load.scope);((Map<String,Object>)((List<?>)changedScope.get("boards")).get(0)).put("board_load_id",UUID.randomUUID().toString());expect("INSPECTION_STALE",()->loadBound.revalidate(config,load.job,changedScope,load.context,load.loadedId));
        passed.add("stale ownership/context, full orphan/false placed history, unrelated native graph drift, same-byte native identity substitution, Part content and board-load binding");
    }
    static void invalidInputs()throws Exception {
        Fixture f=new Fixture(2,true);NativeLoadedBoardInspection.Snapshot snapshot=f.capture();
        for(String field:List.of("bridge_instance_id","job_id","job_revision","config_revision","board_load_revision","lineage_id","lineage_revision","ownership_epoch","job_state")){Map<String,Object> context=new LinkedHashMap<>(f.context);context.remove(field);expect("INSPECTION_CONTEXT",()->NativeLoadedBoardInspection.capture(config,f.job,f.scope,context,f.loadedId));}
        for(Object invalid:List.of("1",new BigDecimal("1.5"),-1,Double.NaN)){Map<String,Object> context=new LinkedHashMap<>(f.context);context.put("ownership_epoch",invalid);expect("INSPECTION_CONTEXT",()->NativeLoadedBoardInspection.capture(config,f.job,f.scope,context,f.loadedId));}
        for(String presence:List.of("missing","unknown")){JsonObject s=submission(snapshot);row(s,0).addProperty("presence",presence);row(s,0).addProperty("polarity","unknown");expect("INSPECTION_SUBMISSION",()->snapshot.evaluate(s));}
        for(String field:List.of("dx_mm","dy_mm","rotation_deg","xy_uncertainty_mm","rotation_uncertainty_deg")){JsonObject absent=submission(snapshot);row(absent,0).remove(field);expect("INSPECTION_SUBMISSION",()->snapshot.evaluate(absent));JsonObject string=submission(snapshot);row(string,0).addProperty(field,"0");expect("INSPECTION_SUBMISSION",()->snapshot.evaluate(string));}
        for(Number n:List.of(-1,0,Double.NaN,Double.POSITIVE_INFINITY,new BigDecimal("1e-9999"))){JsonObject s=submission(snapshot);s.getAsJsonObject("tolerances").addProperty("xy_mm",n);expect("INSPECTION_SUBMISSION",()->snapshot.evaluate(s));}
        for(String field:List.of("operator_label","operator_note")){JsonObject s=submission(snapshot);s.addProperty(field,"  ");expect("INSPECTION_SUBMISSION",()->snapshot.evaluate(s));}
        JsonObject unknown=submission(snapshot);unknown.addProperty("production_authority_granted",true);expect("INSPECTION_SUBMISSION",()->snapshot.evaluate(unknown));
        JsonObject negative=submission(snapshot);row(negative,0).addProperty("xy_uncertainty_mm",-0.01);expect("INSPECTION_SUBMISSION",()->snapshot.evaluate(negative));
        JsonObject artifact=submission(snapshot);artifact.getAsJsonArray("artifact_refs").add(object("artifact_id",UUID.randomUUID().toString(),"sha256","a".repeat(64),"kind","image"));Map<String,Object> evaluated=snapshot.evaluate(artifact);check(((List<?>)evaluated.get("artifact_refs")).size()==1&&!((Boolean)evaluated.get("artifact_bytes_verified_by_model")),"Model retains bounded artifact provenance without claiming byte verification");
        JsonObject duplicate=copy(artifact);duplicate.getAsJsonArray("artifact_refs").add(copy(duplicate.getAsJsonArray("artifact_refs").get(0)));expect("INSPECTION_SUBMISSION",()->snapshot.evaluate(duplicate));
        for(String key:List.of("artifact_id","sha256","kind")){JsonObject bad=copy(artifact);bad.getAsJsonArray("artifact_refs").get(0).getAsJsonObject().addProperty(key,"/tmp/untrusted");expect("INSPECTION_SUBMISSION",()->snapshot.evaluate(bad));}
        passed.add("strict mandatory trusted context; no numeric-string/nonfinite/negative uncertainty; closed submission/observation/artifact fields; bounded unverified native artifact refs");
    }
    static void limits()throws Exception {Fixture full=new Fixture(200,true);NativeLoadedBoardInspection.Snapshot snapshot=full.capture();check(count(snapshot.preview(),"required_count")==200,"Exact200-placement scope accepted");check(count(snapshot.evaluate(submission(snapshot)),"passed_count")==200,"All200 observations evaluated with exact coverage");Fixture over=new Fixture(201,true);expect("INSPECTION_LIMIT",over::capture);Fixture empty=new Fixture(0,true);expect("INSPECTION_NOT_COMPLETE",empty::capture);JsonObject artifacts=submission(snapshot);for(int i=0;i<33;i++)artifacts.getAsJsonArray("artifact_refs").add(object("artifact_id",UUID.randomUUID().toString(),"sha256","a".repeat(64),"kind","image"));expect("INSPECTION_LIMIT",()->snapshot.evaluate(artifacts));passed.add("exact200-placement positive control and201-placement/33-artifact bounds");}
    static void oldRefusal(String code,Action action)throws Exception {try{action.run();throw new AssertionError("Expected native graph refusal "+code);}catch(NativePlacementEdits.Fault e){check(code.equals(e.code),"Expected graph refusal "+code+", got "+e.code);}}
    static String graph(Job job)throws Exception{return (String)NativePlacementEdits.inspectForLoadedBoardInspection(config,job,0,200).get("source_fingerprint");}
    static void readOnlyPads()throws Exception {
        Fixture f=new Fixture(2,true,true);Board board=f.a.getBoard();BoardPad first=board.getSolderPastePads().get(0);check(first!=board.getDefinition().getSolderPastePads().get(0)&&first.getPad()==board.getDefinition().getSolderPastePads().get(0).getPad(),"Actual native Board copy owns distinct BoardPads and deliberately shared Pad shapes");
        List<BoardPad> before=new ArrayList<>(board.getSolderPastePads());Map<String,Boolean> history=new TreeMap<>(f.job.getPlacedStatusSnapshot());boolean dirty=f.job.isDirty();NativeLoadedBoardInspection.Snapshot snapshot=f.capture();outcome(snapshot,submission(snapshot),"passed");snapshot.revalidate(config,f.job,f.scope,f.context,f.loadedId);check(before.equals(board.getSolderPastePads())&&history.equals(f.job.getPlacedStatusSnapshot())&&dirty==f.job.isDirty(),"Read-only pad inspection preserves native objects, full history and dirty flag");
        oldRefusal("UNSUPPORTED_PASTE_PADS",()->NativePlacementEdits.inspect(config,f.job,0,200));JsonArray changes=new JsonArray();changes.add(object("scope","job_instance","holder_instance_id",f.a.getUniqueId(),"placement_id","R1","set",object("enabled",true)));oldRefusal("UNSUPPORTED_PASTE_PADS",()->NativePlacementEdits.stage(config,f.job,changes));
        Pad.Circle circle=(Pad.Circle)first.getPad();circle.setRadius(0.6);expect("INSPECTION_STALE",()->snapshot.revalidate(config,f.job,f.scope,f.context,f.loadedId));circle.setRadius(0.5);
        NativeLoadedBoardInspection.Snapshot name=f.capture();first.setName("changed pad metadata");expect("INSPECTION_STALE",()->name.revalidate(config,f.job,f.scope,f.context,f.loadedId));first.setName(null);
        NativeLoadedBoardInspection.Snapshot type=f.capture();first.setType(BoardPad.Type.Ignore);expect("INSPECTION_STALE",()->type.revalidate(config,f.job,f.scope,f.context,f.loadedId));first.setType(BoardPad.Type.Paste);
        NativeLoadedBoardInspection.Snapshot identity=f.capture();String fingerprint=graph(f.job);Pad.Circle replacement=new Pad.Circle();replacement.setRadius(circle.getRadius());replacement.setUnits(circle.getUnits());first.setPad(replacement);check(fingerprint.equals(graph(f.job)),"Same-content native shape substitution preserves semantic fingerprint");expect("INSPECTION_STALE",()->identity.revalidate(config,f.job,f.scope,f.context,f.loadedId));first.setPad(circle);
        NativeLoadedBoardInspection.Snapshot padIdentity=f.capture();BoardPad last=board.getSolderPastePads().get(2),newPad=new BoardPad(last);String padFingerprint=graph(f.job);board.removeSolderPastePad(last);board.addSolderPastePad(newPad);check(padFingerprint.equals(graph(f.job)),"Same-content native BoardPad substitution preserves ordered semantic fingerprint");expect("INSPECTION_STALE",()->padIdentity.revalidate(config,f.job,f.scope,f.context,f.loadedId));
        NativeLoadedBoardInspection.Snapshot canonical=f.capture();board.getDefinition().getSolderPastePads().get(0).setName("canonical-only pad change");expect("INSPECTION_STALE",()->canonical.revalidate(config,f.job,f.scope,f.context,f.loadedId));
        passed.add("read-only exact native Circle/Ellipse/RoundRectangle pad inventory; native shared shapes; existing edit/inspect refusals retained; canonical/instance content and same-byte pad/shape identity drift");
    }
    static void padRefusals()throws Exception {
        Fixture aliases=new Fixture(2,true,true);BoardPad same=aliases.a.getBoard().getSolderPastePads().get(0);aliases.b.getBoard().addSolderPastePad(same);oldRefusal("UNSUPPORTED_PASTE_PADS",()->aliases.capture());
        Fixture duplicate=new Fixture(2,true,true);duplicate.a.getBoard().addSolderPastePad(duplicate.a.getBoard().getSolderPastePads().get(0));oldRefusal("UNSUPPORTED_PASTE_PADS",()->duplicate.capture());
        Fixture absent=new Fixture(2,true);absent.a.getBoard().addSolderPastePad(new BoardPad());oldRefusal("UNSUPPORTED_PASTE_PADS",()->absent.capture());
        Fixture custom=new Fixture(2,true);Pad.Circle c=new Pad.Circle();c.setRadius(1);custom.a.getBoard().addSolderPastePad(new BoardPad(c,new Location(LengthUnit.Millimeters)){});oldRefusal("UNSUPPORTED_PASTE_PADS",()->custom.capture());
        Fixture customShape=new Fixture(2,true);customShape.a.getBoard().addSolderPastePad(new BoardPad(new Pad.Circle(){},new Location(LengthUnit.Millimeters)));oldRefusal("UNSUPPORTED_PASTE_PADS",()->customShape.capture());
        Fixture nonfinite=new Fixture(2,true,true);((Pad.Circle)nonfinite.a.getBoard().getSolderPastePads().get(0).getPad()).setRadius(Double.NaN);oldRefusal("UNSUPPORTED_PASTE_PADS",()->nonfinite.capture());
        Fixture noUnits=new Fixture(2,true,true);noUnits.a.getBoard().getSolderPastePads().get(0).getPad().setUnits(null);oldRefusal("UNSUPPORTED_PASTE_PADS",()->noUnits.capture());
        Fixture invalidRoundness=new Fixture(2,true,true);((Pad.RoundRectangle)invalidRoundness.a.getBoard().getSolderPastePads().get(2).getPad()).setRoundness(1.01);oldRefusal("UNSUPPORTED_PASTE_PADS",()->invalidRoundness.capture());
        // Native getter is unmodifiable and addSolderPastePad defensively dereferences entries.
        // Reflection injects only invalid fixture inventory to exercise null and count guards.
        java.lang.reflect.Field field=Board.class.getDeclaredField("solderPastePads");field.setAccessible(true);Fixture nullPad=new Fixture(2,true);ArrayList<BoardPad> nulls=new ArrayList<>();nulls.add(null);field.set(nullPad.a.getBoard(),nulls);oldRefusal("UNSUPPORTED_PASTE_PADS",()->nullPad.capture());
        Fixture bound=new Fixture(2,true);ArrayList<BoardPad> pads=new ArrayList<>();Pad.Circle shared=new Pad.Circle();shared.setRadius(1);for(int i=0;i<100001;i++)pads.add(new BoardPad(shared,new Location(LengthUnit.Millimeters)));field.set(bound.a.getBoard().getDefinition(),pads);oldRefusal("GRAPH_LIMIT",()->bound.capture());
        passed.add("pad subclasses/null/alias/nonfinite/unit/roundness refusals and aggregate100001-pad budget refusal using native fixture inventory");
    }
    static void outputBounds()throws Exception {
        for(Location location:List.of(new Location(LengthUnit.Millimeters,10000.01,0,0,0),new Location(LengthUnit.Millimeters,0,0,1000.01,0),new Location(LengthUnit.Millimeters,0,0,0,720))){Fixture f=new Fixture(2,true);f.a.getBoard().getDefinition().getPlacements().get("R1").setLocation(location);expect("INSPECTION_LIMIT",f::capture);}
        Fixture exact=new Fixture(2,true);exact.a.getBoard().getDefinition().getPlacements().get("R1").setLocation(new Location(LengthUnit.Millimeters,10000,-10000,1000,360));check(count(exact.capture().preview(),"required_count")==2,"Exact documented required-row pose boundaries accepted");
        Part longId=new Part("P".repeat(129));longId.setPackage(part.getPackage());longId.setHeight(part.getHeight());config.addPart(longId);Fixture longPart=new Fixture(2,true);longPart.a.getBoard().getDefinition().getPlacements().get("R1").setPart(longId);expect("INSPECTION_LIMIT",longPart::capture);
        passed.add("model rejects required poses/Part IDs outside durable reducer bounds before task publication");
    }
}
