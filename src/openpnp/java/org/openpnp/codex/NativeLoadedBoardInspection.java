/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import org.openpnp.model.*;

/** A read-only binding between a completed native load and local operator observations.
 * Call capture and revalidate on the owning native executor under the Bridge revision fence.
 * The caller owns local submission admission, artifact verification and durable publication.
 * No reported measurement is authenticated by this model and no result grants production.
 */
public final class NativeLoadedBoardInspection {
    public static final String PROFILE="native-loaded-board-inspection-v1";
    public static final int MAX_PLACEMENTS=200, MAX_ARTIFACTS=32;
    private static final Gson JSON=new Gson();
    private NativeLoadedBoardInspection() { }
    public static final class Fault extends Exception {
        public final String code;
        Fault(String code,String message){super(message);this.code=code;}
        Fault(String code,String message,Throwable cause){super(message,cause);this.code=code;}
    }

    public static Snapshot capture(Configuration configuration,Job job,Map<String,Object> ledgerScope,
            Map<String,Object> context,String loadedBoardId)throws Exception {
        return new Snapshot(configuration,job,ledgerScope,context,loadedBoardId);
    }

    public static final class Snapshot {
        private final Configuration configuration;
        private final Job job;
        private final String loadedBoardId,holderId;
        private final Map<String,Object> preview,context,scope,placedHistory;
        private final List<Object> identities;
        private final List<Map<String,Object>> required;
        private final String graphFingerprint,libraryFingerprint;

        private Snapshot(Configuration c,Job j,Map<String,Object> ledger,Map<String,Object> ctx,String selected)throws Exception {
            if(c==null||c!=Configuration.get()||j==null||j.getClass()!=Job.class)
                fail("INSPECTION_CONTEXT","The current exact native Configuration and Job are required");
            configuration=c;job=j;context=validatedContext(ctx);scope=immutable(ledger);
            loadedBoardId=id(selected,"loaded_board_id",512);
            if(!"native-simulator".equals(scope.get("authority"))||!Boolean.FALSE.equals(scope.get("physical_load_verified"))
                    ||!Objects.equals(context.get("board_load_revision"),scope.get("board_load_revision"))
                    ||!Objects.equals(context.get("job_revision"),scope.get("job_revision")))
                fail("INSPECTION_SCOPE","A matching trusted simulator board-load ledger scope is required");
            id(scope.get("scope_id"),"scope_id",128);
            Object rawBoards=scope.get("boards");if(!(rawBoards instanceof List)||((List<?>)rawBoards).size()>1000)
                fail("INSPECTION_SCOPE","Bounded native board bindings are required");
            Map<?,?> binding=null;Set<String> loadedIds=new HashSet<>(),holderIds=new HashSet<>();
            for(Object raw:(List<?>)rawBoards){
                if(!(raw instanceof Map))fail("INSPECTION_SCOPE","Invalid native board binding");
                Map<?,?> b=(Map<?,?>)raw;String lid=id(b.get("loaded_board_id"),"loaded_board_id",512),hid=id(b.get("board_instance_id"),"board_instance_id",2048);
                if(!loadedIds.add(lid)||!holderIds.add(hid))fail("INSPECTION_IDENTITY","Native board bindings are ambiguous");
                if(lid.equals(selected))binding=b;
            }
            if(binding==null)fail("INSPECTION_SCOPE","Selected loaded board does not exist in the current native scope");
            holderId=id(binding.get("board_instance_id"),"board_instance_id",2048);
            String loadId=id(binding.get("board_load_id"),"board_load_id",512),rootId=id(binding.get("root_instance_id"),"root_instance_id",2048);
            // This validator walks every expanded record and canonical definition, rejecting
            // aliases, custom classes, invalid geometry and ambiguous native parent/ID bindings.
            Map<String,Object> nativeView=NativePlacementEdits.inspectForLoadedBoardInspection(c,j,0,MAX_PLACEMENTS);
            graphFingerprint=(String)nativeView.get("source_fingerprint");
            BoardLocation board=null;
            for(BoardLocation b:j.getBoardLocations())if(holderId.equals(b.getUniqueId())){
                if(board!=null)fail("INSPECTION_IDENTITY","More than one native board has the selected ID");board=b;
            }
            if(board==null||board.getClass()!=BoardLocation.class||board.getBoard().getClass()!=Board.class)
                fail("INSPECTION_IDENTITY","The selected native BoardLocation identity is unavailable");
            String actualRoot=holderId.split(PlacementsHolderLocation.ID_DELIMITTER,2)[0],side=board.getGlobalSide().name().toLowerCase(Locale.ROOT);
            if(!actualRoot.equals(rootId)||!side.equals(binding.get("side"))||!Boolean.valueOf(board.isEnabled()).equals(binding.get("enabled")))
                fail("INSPECTION_SCOPE","Native board side, root or enabled state differs from the trusted load mapping");
            if(!board.isEnabled())fail("INSPECTION_NOT_COMPLETE","The selected loaded board must be enabled");
            required=new ArrayList<>();
            for(Placement p:board.getBoard().getPlacements()){
                if(p.isEnabled()&&p.getType()==Placement.Type.Placement&&p.getSide()==board.getGlobalSide()){
                    if(p.getClass()!=Placement.class||p.getPart()==null)fail("INSPECTION_IDENTITY","Required records need exact native Placement and configured Part identities");
                    for(String nativeId:Arrays.asList(p.getId(),p.getPart().getId()))if(nativeId==null||nativeId.isEmpty()||nativeId.length()>128||nativeId.chars().anyMatch(ch->ch<32||ch==127))
                        fail("INSPECTION_LIMIT","Required native placement/part IDs must contain1..128characters without controls");
                    if(!j.retrievePlacedStatus(board,p.getId()))fail("INSPECTION_NOT_COMPLETE","Required native placement is not known placed: "+holderId+" / "+p.getId());
                    if(required.size()>=MAX_PLACEMENTS)fail("INSPECTION_LIMIT","Inspect a loaded board with at most200 required placements");
                    required.add(immutable(map("holder_instance_id",holderId,"placement_id",p.getId(),"part_id",p.getPart().getId(),
                            "location",pose(p.getLocation()),"native_placed",true)));
                }
            }
            if(required.isEmpty())fail("INSPECTION_NOT_COMPLETE","The selected board has no ordinary required active-side placements");
            if(!NativeBoardLoads.fullHistoryAvailable())fail("INSPECTION_CONTEXT","The pinned complete native placed-history API is required");
            placedHistory=immutable(new TreeMap<String,Object>(j.getPlacedStatusSnapshot()));
            IdentityCapture identity=new IdentityCapture(c);identity.add(c);identity.add(c.getMachine());identity.add(j);identity.location(j.getRootPanelLocation());
            identities=Collections.unmodifiableList(identity.identities);libraryFingerprint=hash(identity.library);
            Map<String,Object> previewValues=map("profile",PROFILE,"loaded_board_id",selected,"board_load_id",loadId,"holder_instance_id",holderId,"root_instance_id",rootId,"board_side",side,
                    "source_fingerprint",graphFingerprint,"library_fingerprint",libraryFingerprint,"context",context,"board_load_scope",scope,
                    "placed_history_fingerprint",hash(placedHistory),
                    "required_count",required.size(),"required_placements",required,"coordinate_frame","holder","location_units","mm","rotation_units","degrees",
                    "authority","local-operator-reported","simulation_only",true,"hardware_qualified",false,"production_authority_granted",false,
                    "instrument_authenticity_verified",false,"limits",map("placements",MAX_PLACEMENTS,"artifact_refs",MAX_ARTIFACTS));
            previewValues.put("scope_fingerprint",hash(previewValues));preview=immutable(previewValues);
        }

        /** Every nested map/list is immutable; no native object or mutable JSON is exposed. */
        public Map<String,Object> preview(){return preview;}

        public void revalidate(Configuration c,Job j,Map<String,Object> ledgerScope,Map<String,Object> currentContext,String selected)throws Exception {
            if(c!=configuration||j!=job||!loadedBoardId.equals(selected))fail("INSPECTION_STALE","Native Configuration, Job or loaded board identity changed");
            final Snapshot now;
            try{now=new Snapshot(c,j,ledgerScope,currentContext,selected);}
            catch(Exception cause){throw new Fault("INSPECTION_STALE","The captured native inspection scope is no longer current",cause);}
            if(!context.equals(now.context)||!scope.equals(now.scope)||!placedHistory.equals(now.placedHistory)
                    ||!graphFingerprint.equals(now.graphFingerprint)||!libraryFingerprint.equals(now.libraryFingerprint)
                    ||!sameIdentities(identities,now.identities))
                fail("INSPECTION_STALE","Native identity, graph, history, library or trusted context changed after inspection capture");
        }

        /** Pure evaluation. Caller must revalidate again after verifying artifact bytes and
         * immediately before committing the result, on the owning native executor. */
        public Map<String,Object> evaluate(JsonObject submission)throws Exception {
            if(submission==null)fail("INSPECTION_SUBMISSION","A local operator submission is required");
            if(submission.toString().getBytes(StandardCharsets.UTF_8).length>256*1024)fail("INSPECTION_LIMIT","Inspection submission exceeds256KiB");
            fields(submission,"operator_label","operator_note","tolerances","records","artifact_refs");
            String operator=string(submission,"operator_label",128),note=string(submission,"operator_note",4000);
            JsonObject tolerance=object(submission.get("tolerances"));fields(tolerance,"xy_mm","rotation_deg");
            double xyTolerance=number(tolerance,"xy_mm",0,100,true),rotationTolerance=number(tolerance,"rotation_deg",0,180,true);
            JsonArray records=array(submission.get("records"));
            if(records.size()!=required.size())fail("INSPECTION_COVERAGE","Every required placement must have exactly one observation");
            JsonArray artifacts=array(submission.get("artifact_refs"));if(artifacts.size()>MAX_ARTIFACTS)fail("INSPECTION_LIMIT","At most32 artifact references are accepted");
            Set<String> artifactIds=new HashSet<>();List<Object> refs=new ArrayList<>();
            for(JsonElement raw:artifacts){JsonObject ref=object(raw);fields(ref,"artifact_id","sha256","kind");String aid=string(ref,"artifact_id",36),sha=string(ref,"sha256",64),kind=string(ref,"kind",32);
                try{if(!UUID.fromString(aid).toString().equals(aid))throw new IllegalArgumentException();}catch(IllegalArgumentException e){fail("INSPECTION_SUBMISSION","Artifact IDs must be canonical UUIDs");}
                if(!sha.matches("[a-f0-9]{64}")||!Set.of("image","measurement-file","operator-note").contains(kind)||!artifactIds.add(aid))fail("INSPECTION_SUBMISSION","Artifact reference kind, hash or uniqueness is invalid");
                refs.add(map("artifact_id",aid,"sha256",sha,"kind",kind));}
            Map<String,JsonObject> observations=new LinkedHashMap<>();Set<String> requiredIds=new HashSet<>();for(Map<String,Object> p:required)requiredIds.add((String)p.get("placement_id"));
            for(JsonElement raw:records){JsonObject row=object(raw);String holder=string(row,"holder_instance_id",2048),placement=string(row,"placement_id",128);
                if(!holderId.equals(holder)||!requiredIds.contains(placement)||observations.put(placement,row)!=null)
                    fail("INSPECTION_COVERAGE","Unexpected, duplicate or cross-instance placement observation");}
            List<Object> results=new ArrayList<>();int passed=0,failed=0,uncertain=0;
            for(Map<String,Object> requiredRow:required){String pid=(String)requiredRow.get("placement_id");JsonObject row=observations.get(pid);
                String presence=string(row,"presence",16),polarity=string(row,"polarity",32);
                if(!Set.of("present","missing","unknown").contains(presence)||!Set.of("correct","incorrect","unknown","not_applicable").contains(polarity))fail("INSPECTION_SUBMISSION","Unsupported presence or polarity observation");
                List<String> failures=new ArrayList<>(),uncertainties=new ArrayList<>();Map<String,Object> measured=new LinkedHashMap<>();
                if(presence.equals("present")){
                    fields(row,"holder_instance_id","placement_id","presence","polarity","dx_mm","dy_mm","rotation_deg","xy_uncertainty_mm","rotation_uncertainty_deg");
                    double dx=number(row,"dx_mm",-1000000,1000000,false),dy=number(row,"dy_mm",-1000000,1000000,false),rotation=number(row,"rotation_deg",-360,360,false);
                    double ux=number(row,"xy_uncertainty_mm",0,1000000,false),ur=number(row,"rotation_uncertainty_deg",0,180,false);
                    double xy=Math.hypot(dx,dy),angle=Math.abs(Math.IEEEremainder(rotation,360));
                    double xyLow=Math.max(0,xy-ux),xyHigh=xy+ux,rLow=Math.max(0,angle-ur),rHigh=Math.min(180,angle+ur);
                    // Classify against original decimal inputs. A double-rounded hypotenuse
                    // or boundary sum must not turn a marginal failure into a passing result.
                    BigDecimal tx=decimal(tolerance,"xy_mm"),tu=decimal(row,"xy_uncertainty_mm"),x=decimal(row,"dx_mm"),y=decimal(row,"dy_mm");
                    BigDecimal squared=x.multiply(x).add(y.multiply(y)),outer=tx.add(tu),inner=tx.subtract(tu);
                    if(squared.compareTo(outer.multiply(outer))>0)failures.add("xy_out_of_tolerance");
                    else if(inner.signum()<0||squared.compareTo(inner.multiply(inner))>0)uncertainties.add("xy_uncertainty_crosses_tolerance");
                    BigDecimal a=decimal(row,"rotation_deg").abs().remainder(BigDecimal.valueOf(360));if(a.compareTo(BigDecimal.valueOf(180))>0)a=BigDecimal.valueOf(360).subtract(a);
                    BigDecimal ru=decimal(row,"rotation_uncertainty_deg"),rt=decimal(tolerance,"rotation_deg"),lower=a.subtract(ru).max(BigDecimal.ZERO),upper=a.add(ru).min(BigDecimal.valueOf(180));
                    if(lower.compareTo(rt)>0)failures.add("rotation_out_of_tolerance");else if(upper.compareTo(rt)>0)uncertainties.add("rotation_uncertainty_crosses_tolerance");
                    measured.putAll(map("dx_mm",decimal(row,"dx_mm"),"dy_mm",decimal(row,"dy_mm"),"rotation_deg",decimal(row,"rotation_deg"),"xy_uncertainty_mm",decimal(row,"xy_uncertainty_mm"),"rotation_uncertainty_deg",decimal(row,"rotation_uncertainty_deg"),
                            "xy_error_mm",xy,"rotation_error_deg",angle,"xy_interval_mm",List.of(xyLow,xyHigh),"rotation_interval_deg",List.of(rLow,rHigh)));
                }else{
                    fields(row,"holder_instance_id","placement_id","presence","polarity");
                    if(!polarity.equals("unknown"))fail("INSPECTION_SUBMISSION","A missing or unknown component cannot have a known polarity observation");
                    if(presence.equals("missing"))failures.add("component_missing");else uncertainties.add("presence_unknown");
                }
                if(polarity.equals("incorrect"))failures.add("polarity_incorrect");else if(polarity.equals("unknown"))uncertainties.add("polarity_unknown");
                String outcome=!failures.isEmpty()?"failed":!uncertainties.isEmpty()?"uncertain":"passed";
                if(outcome.equals("passed"))passed++;else if(outcome.equals("failed"))failed++;else uncertain++;
                results.add(map("holder_instance_id",holderId,"placement_id",pid,"presence",presence,"polarity",polarity,"measurements",measured,
                        "outcome",outcome,"failures",failures,"uncertainties",uncertainties,"native_placed",true));
            }
            return immutable(map("profile",PROFILE,"loaded_board_id",loadedBoardId,"holder_instance_id",holderId,"source_fingerprint",graphFingerprint,"scope_fingerprint",preview.get("scope_fingerprint"),
                    "operator_label",operator,"operator_note",note,"tolerances",map("xy_mm",decimal(tolerance,"xy_mm"),"rotation_deg",decimal(tolerance,"rotation_deg")),"artifact_refs",refs,
                    "required_count",required.size(),"observed_count",results.size(),"complete_coverage",true,"passed_count",passed,"failed_count",failed,"uncertain_count",uncertain,
                    "outcome",failed>0?"failed":uncertain>0?"uncertain":"passed","records",results,"authority","local-operator-reported",
                    "polarity_applicability","operator-reported","instrument_authenticity_verified",false,"artifact_bytes_verified_by_model",false,
                    "native_placed_history_modified",false,"production_authority_granted",false,"simulation_only",true,"hardware_qualified",false));
        }
    }

    private static final class IdentityCapture {
        final Configuration configuration;final List<Object> identities=new ArrayList<>(),library=new ArrayList<>();
        final Set<Object> visited=Collections.newSetFromMap(new IdentityHashMap<>());
        IdentityCapture(Configuration configuration){this.configuration=configuration;}
        boolean add(Object o)throws Exception {if(o==null)return false;if(identities.size()>100000)fail("INSPECTION_LIMIT","Native identity inventory exceeds bound");if(!visited.add(o))return false;identities.add(o);return true;}
        void location(PlacementsHolderLocation<?> location)throws Exception {if(!add(location))return;holder(location.getPlacementsHolder());if(location.getClass()==PanelLocation.class)for(PlacementsHolderLocation<?> child:((PanelLocation)location).getChildren())location(child);}
        void holder(PlacementsHolder<?> holder)throws Exception {if(!add(holder))return;holderDefinition(holder.getDefinition());for(Placement p:holder.getPlacements())placement(p);if(holder.getClass()==Board.class)for(BoardPad pad:((Board)holder).getSolderPastePads()){add(pad);add(pad.getPad());}if(holder.getClass()==Panel.class){Panel panel=(Panel)holder;for(Placement p:panel.getPseudoPlacements())placement(p);for(PlacementsHolderLocation<?> child:panel.getChildren())location(child);}}
        void holderDefinition(PlacementsHolder<?> definition)throws Exception {holder(definition);}
        void placement(Placement placement)throws Exception {if(!add(placement))return;if(placement.getDefinition()!=placement)placement(placement.getDefinition());Part part=placement.getPart();if(part==null)return;
            if(part.getClass()!=Part.class||configuration.getPart(part.getId())!=part||part.getPackage()==null||part.getPackage().getClass()!=org.openpnp.model.Package.class||configuration.getPackage(part.getPackage().getId())!=part.getPackage())fail("INSPECTION_IDENTITY","Native Part/Package registry identities differ");
            if(add(part))library.add(map("part_id",part.getId(),"sha256",libraryHash(part)));if(add(part.getPackage()))library.add(map("package_id",part.getPackage().getId(),"sha256",libraryHash(part.getPackage())));
        }
    }

    private static Map<String,Object> validatedContext(Map<String,Object> supplied)throws Exception {
        Map<String,Object> context=immutable(supplied);
        for(String key:List.of("bridge_instance_id","job_id","job_revision","config_revision","board_load_revision","lineage_id"))id(context.get(key),key,512);
        if(!((String)context.get("config_revision")).matches("cfg-[0-9]+")||!((String)context.get("board_load_revision")).matches("load-[0-9]+")||!"completed".equals(context.get("job_state")))fail("INSPECTION_CONTEXT","A completed job and explicit native configuration/load revisions are required");
        for(String key:List.of("lineage_revision","ownership_epoch"))try{NativeJournalJson.integer(context.get(key),0,9007199254740991L);}catch(IllegalArgumentException e){throw new Fault("INSPECTION_CONTEXT",key+" must be an exact nonnegative safe integer",e);}
        return context;
    }
    private static String libraryHash(Object object)throws Exception {
        java.io.ByteArrayOutputStream bytes=new java.io.ByteArrayOutputStream(){private void bound(int n){if(n<0||n>8*1024*1024-count)throw new IllegalStateException("Inspection native library exceeds8MiB");}@Override public synchronized void write(int b){bound(1);super.write(b);}@Override public synchronized void write(byte[] b,int offset,int n){bound(n);super.write(b,offset,n);}};
        Configuration.createSerializer().write(object,bytes);return digest(bytes.toByteArray());
    }
    private static boolean sameIdentities(List<Object> a,List<Object> b){if(a.size()!=b.size())return false;for(int i=0;i<a.size();i++)if(a.get(i)!=b.get(i))return false;return true;}
    private static Map<String,Object> pose(Location nativeLocation)throws Exception {Location l=nativeLocation.convertToUnits(LengthUnit.Millimeters);if(!Double.isFinite(l.getX())||!Double.isFinite(l.getY())||!Double.isFinite(l.getZ())||!Double.isFinite(l.getRotation())||Math.abs(l.getX())>10000||Math.abs(l.getY())>10000||Math.abs(l.getZ())>1000||Math.abs(l.getRotation())>360)fail("INSPECTION_LIMIT","Required native placement pose exceeds10000mm XY,1000mm Z or360degrees");return map("x",l.getX(),"y",l.getY(),"z",l.getZ(),"rotation",l.getRotation(),"units","mm");}
    private static Map<String,Object> immutable(Map<?,?> value)throws Exception {if(value==null)fail("INSPECTION_CONTEXT","Missing immutable context/scope");try{return (Map<String,Object>)freeze(NativeJournalJson.copy(value));}catch(RuntimeException e){throw new Fault("INSPECTION_CONTEXT","Invalid JSON-safe context/scope",e);}}
    private static Object freeze(Object value){if(value instanceof Map){Map<String,Object> out=new LinkedHashMap<>();for(Map.Entry<?,?> e:((Map<?,?>)value).entrySet())out.put((String)e.getKey(),freeze(e.getValue()));return Collections.unmodifiableMap(out);}if(value instanceof List){List<Object> out=new ArrayList<>();for(Object item:(List<?>)value)out.add(freeze(item));return Collections.unmodifiableList(out);}return value;}
    private static Map<String,Object> map(Object... pairs){Map<String,Object> out=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)out.put((String)pairs[i],pairs[i+1]);return out;}
    private static String hash(Object value)throws Exception{return digest(JSON.toJson(value).getBytes(StandardCharsets.UTF_8));}
    private static String digest(byte[] bytes)throws Exception {StringBuilder s=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(bytes))s.append(String.format("%02x",b));return s.toString();}
    private static void fail(String code,String message)throws Fault {throw new Fault(code,message);}
    private static String id(Object value,String key,int max)throws Exception {if(!(value instanceof String)||((String)value).trim().isEmpty()||((String)value).length()>max||((String)value).chars().anyMatch(c->c<32||c==127))fail("INSPECTION_CONTEXT","Invalid "+key);return (String)value;}
    private static JsonObject object(JsonElement element)throws Exception {if(element==null||!element.isJsonObject())fail("INSPECTION_SUBMISSION","Expected an object");return element.getAsJsonObject();}
    private static JsonArray array(JsonElement element)throws Exception {if(element==null||!element.isJsonArray())fail("INSPECTION_SUBMISSION","Expected an array");return element.getAsJsonArray();}
    private static void fields(JsonObject object,String... names)throws Exception {Set<String> allowed=new HashSet<>(Arrays.asList(names));for(Map.Entry<String,JsonElement> e:object.entrySet())if(!allowed.contains(e.getKey()))fail("INSPECTION_SUBMISSION","Unknown field: "+e.getKey());}
    private static String string(JsonObject object,String key,int max)throws Exception {JsonElement value=object.get(key);if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isString())fail("INSPECTION_SUBMISSION","Expected string: "+key);String s=value.getAsString();if(s.trim().isEmpty()||s.length()>max||s.chars().anyMatch(c->(c<32&&c!='\n'&&c!='\t')||c==127))fail("INSPECTION_SUBMISSION","Invalid string: "+key);return s;}
    private static BigDecimal decimal(JsonObject object,String key)throws Exception {JsonElement value=object.get(key);if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isNumber())fail("INSPECTION_SUBMISSION","Expected JSON number: "+key);
        try{String raw=value.getAsString();if(raw.length()>128)throw new NumberFormatException();BigDecimal number=new BigDecimal(raw);if(Math.abs((long)number.scale())>1000)throw new NumberFormatException();return number;}catch(NumberFormatException e){throw new Fault("INSPECTION_SUBMISSION","Invalid finite number: "+key,e);}}
    private static double number(JsonObject object,String key,double low,double high,boolean exclusiveLow)throws Exception {BigDecimal value=decimal(object,key);double result=value.doubleValue();int lowComparison=value.compareTo(BigDecimal.valueOf(low));if(!Double.isFinite(result)||(result==0&&value.signum()!=0)||lowComparison<0||value.compareTo(BigDecimal.valueOf(high))>0||(exclusiveLow&&lowComparison==0))fail("INSPECTION_SUBMISSION","Number outside bounds: "+key);return result;}
}
