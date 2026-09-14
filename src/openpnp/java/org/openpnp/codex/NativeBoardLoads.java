/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.model.Abstract2DLocatable.Side;

/** Native simulator load identities. All mutation is on the owning machine executor.
 * The shared bridge journal is the sole persistent authority; snapshot() never reads a live Job.
 * A returned native hook is not independent inspection or proof of physical board loading.
 */
public final class NativeBoardLoads {
    public interface Sink { void append(String type,Map<String,Object> payload)throws Exception; }
    public static final int MAX_LOADS=512,MAX_RETAINED_BOARDS=4096,MAX_HISTORY=100000,MAX_BOARDS=1000,MAX_PLACEMENTS=10000;
    private static final Gson GSON=new Gson();
    private final Sink sink;
    private final Map<String,Map<String,Object>> loads=new LinkedHashMap<>();
    private final Map<String,String> active=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> pending=new LinkedHashMap<>();
    private Map<String,Root> roots=new LinkedHashMap<>();
    private final Set<String> confirmed=new HashSet<>();
    private final Set<String> restartUnconfirmedLoads=new HashSet<>();
    private String jobId,jobRevision;private Job boundNativeJob;
    private ContinuationBinding continuationBinding;
    /** Process-local staging only. A forced outer publication and its live success witness
     * must both exist before this binding can satisfy requireReady. Replay never creates it. */
    private static final class ContinuationBinding {
        final NativeFaultedJobReplacement.Publication publication;final Job oldJob,freshJob;final String attempt;
        final Map<String,Object> authority;final Map<String,String> loadIds;final Map<String,Root> nativeRoots;
        ContinuationBinding(NativeFaultedJobReplacement.Publication publication,Job oldJob,Job freshJob,String attempt,Map<String,Object> authority,Map<String,String> loadIds,Map<String,Root> roots){this.publication=publication;this.oldJob=oldJob;this.freshJob=freshJob;this.attempt=attempt;this.authority=authority;this.loadIds=Collections.unmodifiableMap(new LinkedHashMap<>(loadIds));nativeRoots=Collections.unmodifiableMap(new LinkedHashMap<>(roots));}
    }
    private final Map<String,Map<String,Object>> replacementIntents=new LinkedHashMap<>(),replacementReceipts=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> continuationAdoptions=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> continuationStepIntents=new LinkedHashMap<>(),continuationStepOutcomes=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> restartObservations=new LinkedHashMap<>();
    private final Map<String,RestartWitness> restartWitnesses=new LinkedHashMap<>();
    private boolean restartPublicationFault;
    private static final class RestartWitness {
        final NativeFaultedJobReplacement.RestartPermit permit;final Job oldJob,freshJob;final Map<String,Object> receipt;
        RestartWitness(NativeFaultedJobReplacement.RestartPermit p,Job old,Job fresh,Map<String,Object> r){permit=p;oldJob=old;freshJob=fresh;receipt=r;}
    }
    private boolean continuationPublicationFault;
    private final Map<String,String> quarantinedJobs=new LinkedHashMap<>(),quarantinedLoads=new LinkedHashMap<>();
    private final Set<Job> quarantinedNativeJobs=Collections.newSetFromMap(new IdentityHashMap<>());
    private volatile long revision;
    private volatile Map<String,Object> snapshot=immutable(map("board_load_revision","load-0","roots",List.of(),"loads",List.of(),"authority","native-simulator","physical_load_verified",false));
    private boolean recovered;
    public static boolean fullHistoryAvailable(){try{return Job.hasBoardLoadHistoryApi();}catch(NoSuchMethodError missing){return false;}}

    public NativeBoardLoads(Sink sink){this.sink=Objects.requireNonNull(sink);}
    public String revision(){return "load-"+revision;}
    public String jobRevision(){return jobRevision;}
    public Map<String,Object> snapshot(){return snapshot;}

    /** One pure predicate is used before forced publication and during replay. */
    private Map<String,Object> validateEvent(String type,Map<String,Object> payload)throws Exception {
        Map<String,Object> next=record(payload.get("record"));String load=(String)next.get("load_id");
        if("board_load_intent".equals(type)) {
            if(number(payload.get("revision"))!=revision+1||pending.containsKey(id(payload.get("change_id")))||!"loading_unknown".equals(next.get("state")))throw new IllegalArgumentException("Invalid board-load intent transition");
            if(!Objects.equals(active.get(next.get("root_instance_id")),payload.get("previous_load_id")))throw new IllegalArgumentException("Board-load intent changed its prior root binding");
            Map<String,Object> prior=loads.get(load);
            if(prior!=null)validateContinuity(prior,next,true);
        } else if("board_load_outcome".equals(type)) {
            Map<String,Object> intent=pending.get(id(payload.get("change_id")));
            if(intent==null||number(payload.get("revision"))!=number(intent.get("revision"))||!"loaded".equals(next.get("state")))throw new IllegalArgumentException("Board-load outcome has no matching intent");
            validateContinuity(record(intent.get("record")),next,false);
        } else if("board_load_history".equals(type)) {
            Map<String,Object> prior=loads.get(load);if(prior==null)throw new IllegalArgumentException("Unbound board-load history");
            validateContinuity(prior,next,false);
        } else throw new IllegalArgumentException("Unknown board-load journal record");
        int count=0,boardCount=0;for(Map<String,Object> prior:loads.values())if(!Objects.equals(prior.get("load_id"),load)){count+=((Map<?,?>)prior.get("placed_history")).size();boardCount+=((Map<?,?>)prior.get("board_ids")).size();}
        count+=((Map<?,?>)next.get("placed_history")).size();boardCount+=((Map<?,?>)next.get("board_ids")).size();
        if((!loads.containsKey(load)&&loads.size()>=MAX_LOADS)||count>MAX_HISTORY||boardCount>MAX_RETAINED_BOARDS)throw fault("BOARD_LOAD_CAPACITY","Retained placement-history capacity reached");
        return next;
    }
    private static void validateContinuity(Map<String,Object> prior,Map<String,Object> next,boolean allowSideChange) {
        for(String field:List.of("load_id","root_instance_id","signature","board_ids","history_encoding","complete_native_history"))if(!Objects.equals(prior.get(field),next.get(field)))throw new IllegalArgumentException("Board-load identity changed: "+field);
        if(!allowSideChange&&!Objects.equals(prior.get("side"),next.get("side")))throw new IllegalArgumentException("Board-load side changed outside a change intent");
        String regression=historyRegression((Map<?,?>)prior.get("placed_history"),(Map<?,?>)next.get("placed_history"));
        if(regression!=null)throw new IllegalArgumentException(regression);
    }
    /** Native progress may add keys or advance false to true, but cannot erase retained facts. */
    private static String historyRegression(Map<?,?> retained,Map<?,?> current) {
        if(!current.keySet().containsAll(retained.keySet()))return "Regressing native placed-history keys";
        for(Map.Entry<?,?> entry:retained.entrySet())if(Boolean.TRUE.equals(entry.getValue())&&!Boolean.TRUE.equals(current.get(entry.getKey())))return "Regressing native placed-history outcome";
        return null;
    }
    private void applyEvent(String type,Map<String,Object> payload,Map<String,Object> record) {
        String load=(String)record.get("load_id");
        if("board_load_intent".equals(type)){pending.put((String)payload.get("change_id"),copy(payload));revision=((Number)payload.get("revision")).longValue();active.put((String)record.get("root_instance_id"),load);}
        else if("board_load_outcome".equals(type))pending.remove((String)payload.get("change_id"));
        loads.put(load,record);refresh();
    }
    /** No native mutation during replay. The Bridge supplies its entire integrity-checked journal. */
    public void recoverEvent(String type,Map<String,Object> payload)throws Exception {
        if(type.equals("board_restart_observation")){prepareRestartObservation(payload).run();return;}
        if(type.equals("board_continuation_adoption")){prepareContinuationAdoption(payload).run();return;}
        if(type.equals("board_continuation_intent")||type.equals("board_continuation_outcome")){prepareContinuationStep(type,payload).run();return;}
        if(type.startsWith("board_replacement_")){prepareReplacement(type,payload).run();return;}
        if(type.startsWith("board_load_"))applyEvent(type,payload,validateEvent(type,payload));
    }
    public void finishRecovery(){
        recovered=!loads.isEmpty();
        // Keep forced records exact for replacement-parent verification. Presence is a new runtime
        // observation requirement, not a rewrite of a historical successful load receipt.
        confirmed.clear();boundNativeJob=null;continuationBinding=null;restartWitnesses.clear();
        for(Map<String,Object> load:loads.values())if("loaded".equals(load.get("state")))restartUnconfirmedLoads.add((String)load.get("load_id"));
        refresh();
    }
    private void append(String type,Map<String,Object> payload)throws Exception{
        Map<String,Object> checked=validateEvent(type,payload);
        sink.append(type,immutable(payload));applyEvent(type,payload,checked);
    }
    /** Consume the already-forced native checkpoint, without writing a duplicate journal record. */
    public void observeNativeEvent(String type,Map<String,Object> payload)throws Exception {
        if(!"native_placement_checkpoint".equals(type)||!"native-placement-complete-hook".equals(payload.get("state")))return;
        Object raw=payload.get("context");if(!(raw instanceof Map))return;Map<?,?> context=(Map<?,?>)raw;
        if(!Boolean.TRUE.equals(context.get("native_placed_status"))||!(context.get("board_load_id") instanceof String))return;
        Map<String,Object> load=loads.get(context.get("board_load_id"));if(load==null)throw new IllegalArgumentException("Placement refers to unknown board load");
        Object board=context.get("board_instance_id");
        if(!Objects.equals(((Map<?,?>)load.get("board_ids")).get(board),context.get("loaded_board_id")))throw new IllegalArgumentException("Placement board identity disagrees with durable load");
        Map<String,Object> history=(Map<String,Object>)load.get("placed_history");String key=board+PlacementsHolderLocation.ID_DELIMITTER+context.get("placement_id");
        history.put(key,true);checkCapacity();
        // The immutable public snapshot is refreshed at a bounded native checkpoint cadence.
    }
    public boolean hasActiveUnknown(){for(String loadId:active.values()){Map<String,Object> load=loads.get(loadId);if(load!=null&&"loading_unknown".equals(load.get("state")))return true;}return false;}
    public void publishSnapshot(){refresh();}

    /** Fresh import explicitly creates new virtual boards; restored documents never imply presence. */
    public void bindJob(Job job,String id,boolean freshSimulatorImport)throws Exception {
        continuationBinding=null;
        if(quarantinedJobs.containsKey(id)||quarantinedNativeJobs.contains(job))throw fault("BOARD_LOAD_QUARANTINED","The old faulted board cannot be rebound");
        boundNativeJob=job;roots=inspect(job);confirmed.clear();jobId=id;jobRevision=signature(roots.values().stream().map(r->r.signature).toArray());
        refresh();
        if(freshSimulatorImport&&!recovered&&!hasActiveUnknown())for(Root root:roots.values()){
            String prior=active.get(root.id);
            change(job,root.id,"replace",root.location.getGlobalSide().name().toLowerCase(Locale.ROOT),prior,revision(),true);
        }
    }
    /** Conservative edit invalidation. An edited graph requires explicit same-load/replace binding. */
    public void jobChanged(Job job,String id)throws Exception{bindJob(job,id,false);invalidateTransforms(job);}

    public Map<String,Object> change(Job job,String rootId,String action,String sideName,String expectedLoadId,String expectedRevision,boolean initial)throws Exception {
        if(!initial&&!fullHistoryAvailable())throw fault("BOARD_LOAD_API_UNAVAILABLE","Explicit changeover requires the native complete-history API; stock compatibility covers initial simulator imports only");
        if(!revision().equals(expectedRevision))throw fault("BOARD_LOAD_REVISION_CONFLICT","Board load changed");
        Map<String,Root> current=inspect(job);Root root=current.get(rootId);
        if(root==null)throw fault("BOARD_INSTANCE_NOT_FOUND","Select an exact native root instance from get_board_loads");
        Side side="top".equals(sideName)?Side.Top:"bottom".equals(sideName)?Side.Bottom:null;
        if(side==null)throw fault("INVALID_ARGUMENT","side must be top or bottom");
        if(!Set.of("replace","flip","same-load").contains(action))throw fault("INVALID_ARGUMENT","Unknown load action");
        String previousId=active.get(rootId);Map<String,Object> previous=loads.get(previousId);
        if(!Objects.equals(previousId,expectedLoadId))throw fault("BOARD_LOAD_ID_CONFLICT","Expected load does not match retained native root binding");
        if(!"replace".equals(action)){
            if(previous==null)throw fault("BOARD_LOAD_REQUIRED","No retained load can be reused");
            if("loading_unknown".equals(previous.get("state")))throw fault("BOARD_LOAD_UNKNOWN","Interrupted loading cannot be accepted as the same board");
            if(!root.signature.equals(previous.get("signature")))throw fault("BOARD_LOAD_JOB_MISMATCH","Native board definition or pose changed; explicit replacement is required");
            boolean sameSide=sideName.equals(previous.get("side"));
            if("flip".equals(action)&&sameSide)throw fault("BOARD_LOAD_SIDE_CONFLICT","Flip must select the opposite retained side");
            if("same-load".equals(action)&&(!sameSide||root.location.getGlobalSide()!=side))throw fault("BOARD_LOAD_SIDE_CONFLICT","Same-load requires the exact retained and native side");
            if(!history(job,root).equals(previous.get("placed_history")))throw fault("BOARD_LOAD_HISTORY_MISMATCH","Native placed history differs from the retained load; do not silently replay or erase it");
        }
        if("replace".equals(action)&&loads.size()>=MAX_LOADS)throw fault("BOARD_LOAD_CAPACITY","Retained board-load capacity reached; preserve history and start a new owned simulator store");
        if("replace".equals(action)){
            Map<String,Object> currentHistory=history(job,root);
            if(previous==null&&!currentHistory.isEmpty())throw fault("BOARD_LOAD_UNBOUND_HISTORY","Retain and explicitly reconcile pre-existing native history before assigning a new simulator load");
            if(previous!=null){
                // Preserve every previous native key, including removed placements/descendants.
                // The new load alone starts empty; retired evidence never regresses.
                Map<String,Object> retained=copy(previous),history=new TreeMap<>((Map<String,Object>)previous.get("placed_history"));
                for(Map.Entry<String,Object> entry:currentHistory.entrySet())if(!Boolean.TRUE.equals(history.get(entry.getKey())))history.put(entry.getKey(),entry.getValue());
                retained.put("placed_history",history);
                if(!retained.equals(previous)){append("board_load_history",map("record",retained));previous=loads.get(previousId);}
            }
        }
        Map<String,Object> next="replace".equals(action)?newLoad(root):copy(previous);
        next.put("state","loading_unknown");next.put("side",sideName);next.put("job_id",jobId);next.put("job_revision",jobRevision);
        next.put("authority","native-simulator");next.put("physical_load_verified",false);next.put("simulator_origin",true);
        next.put("change_action",action);next.put("initial_fixture_registration",initial);next.put("registration","invalidated");
        next.put("load_revision","load-"+(revision+1));next.put("updated_at",Instant.now().toString());
        String change=UUID.randomUUID().toString();long nextRevision=revision+1;
        append("board_load_intent",map("change_id",change,"revision",nextRevision,"record",next,"previous_load_id",previousId,"physical_effect_performed",false));
        // Intent is already durable. If any listener/setter throws, retain loading_unknown.
        if("replace".equals(action)){for(String key:new ArrayList<>(history(job,root).keySet()))job.removePlacedStatus(root.location,key.substring(root.id.length()+PlacementsHolderLocation.ID_DELIMITTER.length()));}
        root.location.setGlobalSide(side);invalidate(root.location);
        roots=inspect(job);root=roots.get(rootId);
        next.put("boards",boards(root,(Map<?,?>)next.get("board_ids")));next.put("placed_history",history(job,root));
        next.put("state","loaded");next.put("registration","invalidated");
        append("board_load_outcome",map("change_id",change,"revision",nextRevision,"record",next,"physical_effect_performed",false));
        confirmed.add(rootId);restartUnconfirmedLoads.remove((String)next.get("load_id"));recovered=restartConfirmationRequired();refresh();
        return map("board_load_revision",revision(),"load",summary(next),"simulator_origin",true,"physical_load_verified",false,"registration","invalidated","requires_validation",true,"fixture_clearance","simulated-only; physical underside clearance unverified");
    }

    public void requireReady(Job job)throws Exception {requireReady(job,false);}
    /** Final completion-thread check; does not initialize OpenPnP's native transform cache. */
    void requireReadyForPublication(Job job,NativeFaultedJobReplacement.Publication publication)throws Exception {
        if(publication==null)throw fault("BOARD_CONTINUATION_BINDING","Exact publication witness required");publication.authorizeCompletedRead(this);requireReady(job,true);publication.authorizeCompletedRead(this);
    }
    private void requireReady(Job job,boolean completedRead)throws Exception {
        boolean continued=continuationBinding!=null&&continuationBinding.freshJob==job&&Objects.equals(continuationBinding.attempt,jobId)&&continuationBinding.publication.bindingReady(this,job,jobId);
        if(continuationBinding!=null&&!continued||requiresContinuationPublication(jobId)&&!continued)throw fault("BOARD_CONTINUATION_BINDING","Continuation board binding has no completed live publication");
        if(continuationPublicationFault||quarantinedNativeJobs.contains(job)||quarantinedJobs.containsKey(jobId)||(!replacementReceipts.isEmpty()&&job!=boundNativeJob)||!pending.isEmpty()||hasCurrentUnresolvedReplacementIntents()||!replacementIntents.isEmpty()&&!continued)throw fault("BOARD_LOAD_QUARANTINED","An exact new native replacement binding is required");
        Map<String,Root> actual=inspect(job,completedRead);if(continued)requireStagedIdentity(continuationBinding,job,jobId,actual);
        for(Root root:actual.values()){
            Map<String,Object> record=loads.get(active.get(root.id));
            if(!confirmed.contains(root.id)||record==null||restartUnconfirmedLoads.contains(record.get("load_id"))||!"loaded".equals(record.get("state")))throw fault("BOARD_LOAD_REQUIRED","Every native root needs an explicit confirmed simulator load");
            if(!root.signature.equals(record.get("signature"))||!root.location.getGlobalSide().name().equalsIgnoreCase((String)record.get("side")))throw fault("BOARD_LOAD_JOB_MISMATCH","Native job shape or side changed after load registration");
            String regression=historyRegression((Map<?,?>)record.get("placed_history"),history(job,root));
            if(regression!=null)throw fault("BOARD_LOAD_HISTORY_MISMATCH",regression+"; retain the native job and durable load evidence before any further run");
        }
        int retained=0;for(Map<String,Object> record:loads.values())retained+=((Map<?,?>)record.get("placed_history")).size();
        for(Root root:actual.values()){Map<String,Object> record=loads.get(active.get(root.id));Set<Object> reserved=new HashSet<>(((Map<?,?>)record.get("placed_history")).keySet());reserved.addAll(history(job,root).keySet());retained-=((Map<?,?>)record.get("placed_history")).size();for(BoardLocation board:root.boards)for(Placement placement:board.getBoard().getPlacements())reserved.add(board.getUniqueId()+PlacementsHolderLocation.ID_DELIMITTER+placement.getId());retained+=reserved.size();}
        if(retained>MAX_HISTORY)throw fault("BOARD_LOAD_CAPACITY","Reserve durable placement-history capacity before another run");
        if(!actual.keySet().equals(roots.keySet()))throw fault("BOARD_LOAD_JOB_MISMATCH","Native root membership changed");
    }
    public void requireRevision(String expected)throws Bridge.Fault {if(!revision().equals(expected))throw fault("BOARD_LOAD_REVISION_CONFLICT","Validated board load changed");}
    public void checkpoint(Job job)throws Exception {
        for(Root root:inspect(job).values()){
            Map<String,Object> load=loads.get(active.get(root.id));if(load==null||!root.signature.equals(load.get("signature")))continue;
            Map<String,Object> updated=copy(load);updated.put("placed_history",history(job,root));
            if(!updated.equals(load))append("board_load_history",map("record",updated));
        }
    }
    public Map<String,Object> ledgerScope(Job job)throws Exception {
        requireReady(job);List<Object> bindings=new ArrayList<>();
        for(Root root:roots.values()){
            Map<String,Object> load=loads.get(active.get(root.id));
            for(Object entry:(List<?>)load.get("boards")){
                Map<String,Object> board=copy((Map<?,?>)entry);board.put("board_load_id",load.get("load_id"));bindings.add(board);
            }
        }
        return immutable(map("scope_id",signature(List.of(jobRevision,revision(),bindings)),"board_load_revision",revision(),"job_revision",jobRevision,"authority","native-simulator","physical_load_verified",false,"boards",bindings));
    }
    public void registrationCompleted(Job job,String configRevision)throws Exception {
        requireReady(job);
        for(Root root:roots.values()){
            Map<String,Object> record=copy(loads.get(active.get(root.id)));record.put("registration",map("state","native-fiducial-call-returned","board_load_revision",revision(),"config_revision",configRevision,"physical_verification",false));append("board_load_history",map("record",record));
        }
    }
    /** Revoke registration evidence without rebinding loads or changing any material/history fact. */
    public void invalidateRegistration(String configRevision,String reason)throws Exception {
        for(String loadId:active.values()){
            Map<String,Object> record=copy(loads.get(loadId));
            record.put("registration",map("state","invalidated","reason",reason,"config_revision",configRevision,"physical_verification",false));
            append("board_load_history",map("record",record));
        }
    }
    public static void invalidateTransforms(Job job){for(PlacementsHolderLocation<?> root:job.getRootPanelLocation().getChildren())invalidate(root);}
    private static void invalidate(PlacementsHolderLocation<?> location){location.setLocalToParentTransform(null);if(location instanceof PanelLocation)for(PlacementsHolderLocation<?> child:((PanelLocation)location).getChildren())invalidate(child);}

    private Map<String,Object> newLoad(Root root)throws Exception {
        Map<String,Object> ids=new LinkedHashMap<>();for(BoardLocation board:root.boards)ids.put(board.getUniqueId(),UUID.randomUUID().toString());
        return map("load_id",UUID.randomUUID().toString(),"root_instance_id",root.id,"signature",root.signature,"board_ids",ids,"boards",boards(root,ids),"placed_history",new LinkedHashMap<>(),"history_encoding","native-scope-key-v1","complete_native_history",fullHistoryAvailable(),"created_at",Instant.now().toString());
    }
    private static List<Object> boards(Root root,Map<?,?> ids){List<Object> result=new ArrayList<>();for(BoardLocation board:root.boards)result.add(map("board_instance_id",board.getUniqueId(),"loaded_board_id",ids.get(board.getUniqueId()),"root_instance_id",root.id,"side",board.getGlobalSide().name().toLowerCase(Locale.ROOT),"enabled",board.isEnabled()));return result;}
    private static Map<String,Object> history(Job job,Root root){
        Map<String,Object> values=new TreeMap<>();String prefix=root.id+PlacementsHolderLocation.ID_DELIMITTER;
        if(fullHistoryAvailable()){for(Map.Entry<String,Boolean> entry:job.getPlacedStatusSnapshot().entrySet())if(entry.getKey().startsWith(prefix))values.put(entry.getKey(),entry.getValue());}
        else for(BoardLocation board:root.boards)for(Placement placement:board.getBoard().getPlacements())if(job.retrievePlacedStatus(board,placement.getId()))values.put(board.getUniqueId()+PlacementsHolderLocation.ID_DELIMITTER+placement.getId(),true);
        return values;
    }
    private void refresh(){
        List<Object> current=new ArrayList<>();for(Root root:roots.values()){Map<String,Object> load=loads.get(active.get(root.id));current.add(map("root_instance_id",root.id,"side",root.location.getGlobalSide().name().toLowerCase(Locale.ROOT),"signature",root.signature,"load_id",load==null?null:load.get("load_id"),"load_state",load==null?"unregistered":restartUnconfirmedLoads.contains(load.get("load_id"))?"presence_unconfirmed":!confirmed.contains(root.id)&&"loaded".equals(load.get("state"))?"job_binding_unconfirmed":load.get("state"),"definition_matches",load!=null&&root.signature.equals(load.get("signature")),"boards",load!=null?load.get("boards"):boards(root,Map.of())));}
        List<Object> retained=new ArrayList<>();for(Map<String,Object> load:loads.values()){Map<String,Object> view=summary(load);if(restartUnconfirmedLoads.contains(load.get("load_id"))&&"loaded".equals(load.get("state")))view.put("state","presence_unconfirmed");retained.add(view);}
        snapshot=immutable(map("board_load_revision",revision(),"job_id",jobId,"job_revision",jobRevision,"roots",current,"loads",retained,"authority","native-simulator","physical_load_verified",false,"restart_presence_confirmation_required",restartConfirmationRequired(),"pending_changes",pending.size(),"replacement_intents",new ArrayList<>(replacementIntents.values()),"replacement_receipts",new ArrayList<>(replacementReceipts.values()),"restart_observation_receipts",new ArrayList<>(restartObservations.values()),"restart_observation_publication_uncertain",restartPublicationFault,"continuation_adoptions",new ArrayList<>(continuationAdoptions.values()),"continuation_publication_fault",continuationPublicationFault,"continuation_step_intents",new ArrayList<>(continuationStepIntents.values()),"continuation_step_outcomes",new ArrayList<>(continuationStepOutcomes.values()),"quarantined_jobs",quarantinedJobs,"quarantined_loads",quarantinedLoads,"complete_native_history",fullHistoryAvailable(),"limits",map("retained_loads",MAX_LOADS,"retained_placed_history",MAX_HISTORY,"boards",MAX_BOARDS,"placements",MAX_PLACEMENTS)));
    }
    private boolean restartConfirmationRequired(){for(String id:active.values())if(restartUnconfirmedLoads.contains(id))return true;return false;}
    private static Map<String,Object> summary(Map<String,Object> record){Map<String,Object> out=new LinkedHashMap<>(record);Map<?,?> h=(Map<?,?>)out.remove("placed_history");out.remove("board_ids");out.put("placed_history_count",h==null?0:h.values().stream().filter(Boolean.TRUE::equals).count());out.put("native_history_entries",h==null?0:h.size());return copy(out);}
    private void checkCapacity(){int entries=0,boards=0;for(Map<String,Object> load:loads.values()){entries+=((Map<?,?>)load.get("placed_history")).size();boards+=((Map<?,?>)load.get("board_ids")).size();}if(entries>MAX_HISTORY||boards>MAX_RETAINED_BOARDS)throw new IllegalArgumentException("Retained board-load history capacity");}
    private static Map<String,Object> record(Object value){
        if(!(value instanceof Map))throw new IllegalArgumentException("Invalid board-load record");
        Map<String,Object> record=copy((Map<?,?>)value);UUID.fromString(id(record.get("load_id")));String root=id(record.get("root_instance_id"));
        if(!(record.get("signature") instanceof String)||!((String)record.get("signature")).matches("[a-f0-9]{64}")||!"native-simulator".equals(record.get("authority"))||!Boolean.FALSE.equals(record.get("physical_load_verified"))||!Boolean.TRUE.equals(record.get("simulator_origin")))throw new IllegalArgumentException("Invalid simulator load authority");
        if(!Set.of("loaded","loading_unknown","presence_unconfirmed").contains(record.get("state"))||!Set.of("top","bottom").contains(record.get("side")))throw new IllegalArgumentException("Invalid native load state");
        if(!(record.get("board_ids") instanceof Map)||!(record.get("boards") instanceof List)||!(record.get("placed_history") instanceof Map))throw new IllegalArgumentException("Invalid native board mapping");
        Map<?,?> ids=(Map<?,?>)record.get("board_ids"),history=(Map<?,?>)record.get("placed_history");List<?> boards=(List<?>)record.get("boards");
        if(ids.size()>MAX_BOARDS||boards.size()!=ids.size()||history.size()>MAX_HISTORY)throw new IllegalArgumentException("Board-load record limit");
        Set<String> uniqueIds=new HashSet<>(),nativeIds=new HashSet<>();
        for(Map.Entry<?,?> entry:ids.entrySet()){id(entry.getKey());String generated=id(entry.getValue());UUID.fromString(generated);if(!uniqueIds.add(generated))throw new IllegalArgumentException("Duplicate loaded board identity");}
        for(Object raw:boards){if(!(raw instanceof Map))throw new IllegalArgumentException("Invalid native board descriptor");Map<?,?> board=(Map<?,?>)raw;String nativeId=id(board.get("board_instance_id"));if(!nativeIds.add(nativeId)||!Objects.equals(ids.get(nativeId),board.get("loaded_board_id"))||!root.equals(board.get("root_instance_id"))||!Set.of("top","bottom").contains(board.get("side")))throw new IllegalArgumentException("Native board descriptor disagrees with its identity inventory");}
        if(!"native-scope-key-v1".equals(record.get("history_encoding")))throw new IllegalArgumentException("Unsupported load history encoding");
        for(Map.Entry<?,?> entry:history.entrySet()){if(!(entry.getKey() instanceof String)||((String)entry.getKey()).length()>8192||!((String)entry.getKey()).startsWith(root+PlacementsHolderLocation.ID_DELIMITTER)||!(entry.getValue() instanceof Boolean))throw new IllegalArgumentException("Invalid scoped native history key");}
        return record;
    }
    /** Complete board records including opaque history; public summaries deliberately omit it. */
    Map<String,Object> captureReplacementState(String id)throws Exception {
        if((jobId!=null&&!Objects.equals(id,jobId))||!fullHistoryAvailable())throw fault("BOARD_LOAD_JOB_MISMATCH","Exact current faulted job and complete native history API required");
        Map<String,Object> associated=new TreeMap<>();for(Map.Entry<String,Map<String,Object>> e:loads.entrySet())if(id.equals(e.getValue().get("job_id")))associated.put(e.getKey(),e.getValue());
        Map<String,Object> current=new TreeMap<>();String capturedRevision=null;for(Map.Entry<String,String> e:active.entrySet())if(associated.containsKey(e.getValue())){current.put(e.getKey(),e.getValue());String rev=(String)loads.get(e.getValue()).get("job_revision");if(capturedRevision!=null&&!capturedRevision.equals(rev))throw fault("BOARD_LOAD_JOB_MISMATCH","Mixed board revisions");capturedRevision=rev;}if(current.isEmpty())throw fault("BOARD_LOAD_REQUIRED","Every faulted root requires its exact retained load");
        return NativeFaultedJobReplacement.frozen(map("job_id",id,"job_revision",capturedRevision,"revision",revision(),"loads",associated,"active",current,"pending_changes",pending,"replacement_intents",replacementIntents));
    }
    static Map<String,Object> replacementModel(Job job)throws Exception {Map<String,Object> result=new TreeMap<>();for(Root root:inspect(job).values())result.put(root.id,root.signature);return NativeFaultedJobReplacement.frozen(result);}
    static void requireDetachedReplacement(Job old,Job fresh)throws Exception {
        if(old==fresh||!fullHistoryAvailable()||!fresh.getPlacedStatusSnapshot().isEmpty()||!replacementModel(old).equals(replacementModel(fresh)))throw fault("BOARD_REPLACEMENT_NOT_FRESH","Replacement must be a distinct native Job with exact requested definitions and empty history");
        Set<Object> seen=Collections.newSetFromMap(new IdentityHashMap<>());nativeObjects(old.getRootPanelLocation(),seen);
        Set<Object> other=Collections.newSetFromMap(new IdentityHashMap<>());nativeObjects(fresh.getRootPanelLocation(),other);for(Object x:other)if(seen.contains(x))throw fault("BOARD_REPLACEMENT_NOT_FRESH","Old and replacement native board/placement objects overlap");
    }
    private static void nativeObjects(Object value,Set<Object> out)throws Exception {
        if(value==null||!out.add(value))return;if(out.size()>100000)throw fault("BOARD_REPLACEMENT_NOT_FRESH","Native definition ownership closure exceeds bound");
        // Native copy constructors retain definition listeners. Include their entire mutable ownership
        // closure, not merely the visible location/placement copies. Library Part/Package sharing is allowed.
        if(value instanceof PlacementsHolderLocation){PlacementsHolderLocation<?> loc=(PlacementsHolderLocation<?>)value;nativeObjects(loc.getDefinition(),out);nativeObjects(loc.getParent(),out);nativeObjects(loc.getPlacementsHolder(),out);if(loc instanceof PanelLocation)for(PlacementsHolderLocation<?> child:((PanelLocation)loc).getChildren())nativeObjects(child,out);}
        else if(value instanceof PlacementsHolder){PlacementsHolder<?> holder=(PlacementsHolder<?>)value;nativeObjects(holder.getDefinition(),out);for(Placement placement:holder.getPlacements())nativeObjects(placement,out);if(holder instanceof Panel)for(PlacementsHolderLocation<?> child:((Panel)holder).getChildren())nativeObjects(child,out);}
        else if(value instanceof Placement)nativeObjects(((Placement)value).getDefinition(),out);
    }
    Map<String,Object> replacementReceipt(String receiptId){return replacementReceipts.get(receiptId);}
    /** Exact durable phases for all original roots. No load presence, registration or native binding
     * is created by this read, including after restart or an interrupted replacement publication. */
    Map<String,Object> replacementProgress(String recoveryOperation,String faultDigest,String replacementJobId,List<String> originalLoadIds)throws Exception {
        NativeFaultedJobReplacement.uuid(recoveryOperation);NativeFaultedJobReplacement.hash(faultDigest);NativeFaultedJobReplacement.uuid(replacementJobId);
        if(originalLoadIds.isEmpty()||originalLoadIds.size()>MAX_LOADS)throw fault("BOARD_LOAD_RECORD","Bounded complete original board union required");
        Set<String> required=new TreeSet<>();for(String id:originalLoadIds)if(!required.add(NativeFaultedJobReplacement.uuid(id)))throw fault("BOARD_LOAD_RECORD","Repeated original board load");
        Map<String,Map<String,Object>> pendingRows=new HashMap<>(),completeRows=new HashMap<>();
        for(Map<String,Object> row:replacementIntents.values())indexReplacementProgress(row,recoveryOperation,faultDigest,replacementJobId,required,pendingRows);
        for(Map<String,Object> row:replacementReceipts.values())indexReplacementProgress(row,recoveryOperation,faultDigest,replacementJobId,required,completeRows);
        List<Object> rows=new ArrayList<>();Map<String,Object> counts=map("untouched",0,"pending",0,"completed",0),effective=map("untouched",0,"pending",0,"completed",0);boolean hasChain=false;
        for(String id:required){Map<String,Object> row=progressRow(recoveryOperation,faultDigest,replacementJobId,id,pendingRows.get(id),completeRows.get(id));rows.add(row);String phase=(String)row.get("phase"),current=(String)row.getOrDefault("effective_phase",phase);counts.put(phase,((Number)counts.get(phase)).intValue()+1);effective.put(current,((Number)effective.get(current)).intValue()+1);hasChain|=row.containsKey("continuation_chain");}
        Map<String,Object> result=map("schema_version",1,"recovery_operation_id",recoveryOperation,"fault_set_sha256",faultDigest,"replacement_job_id",replacementJobId,"board_load_revision",revision(),"original_load_ids",new ArrayList<>(required),"rows",rows,"counts",counts,"original_capture_required_for_untouched",true,"execution_authority_restored",false,"physical_load_verified",false);if(hasChain)result.put("effective_counts",effective);return NativeFaultedJobReplacement.frozen(result);
    }
    private Map<String,Object> progressRow(String operation,String digest,String attempt,String id,Map<String,Object> intent,Map<String,Object> outcome)throws Exception {
        Map<String,Object> old=loads.get(id);if(old==null||intent!=null&&outcome!=null)throw fault("BOARD_LOAD_RECORD","Missing old load or conflicting replacement phases");
        List<Map<String,Object>> chainIntents=new ArrayList<>();for(Map<String,Object> row:continuationStepIntents.values())if(id.equals(row.get("old_load_id"))){if(!operation.equals(row.get("original_recovery_operation_id"))||!digest.equals(row.get("original_fault_set_sha256"))||!attempt.equals(row.get("replacement_attempt_id")))throw fault("BOARD_LOAD_RECORD","Foreign board continuation chain");chainIntents.add(row);}
        Map<String,Object> parent=outcome==null?intent:outcome;String phase=outcome!=null?"completed":intent!=null?"pending":"untouched";
        if(parent!=null){if(!NativeFaultedJobReplacement.same(old,parent.get("old_load"))||!Objects.equals(quarantinedLoads.get(id),parent.get("receipt_id")))throw fault("BOARD_LOAD_HISTORY_MISMATCH","Original quarantined load differs from forced parent");if(outcome!=null){intent=NativeFaultedJobReplacement.mutable(outcome);NativeFaultedJobReplacement.object(intent.get("new_load")).put("state","loading_unknown");}}
        else if(quarantinedLoads.containsKey(id)&&(chainIntents.isEmpty()||!Objects.equals(quarantinedLoads.get(id),chainIntents.get(0).get("step_id"))))throw fault("BOARD_LOAD_RECORD","Original board belongs to a different replacement");
        String root=(String)old.get("root_instance_id"),current=active.get(root);Map<String,Object> fresh=parent==null?null:NativeFaultedJobReplacement.object(parent.get("new_load"));
        Map<String,Object> row=map("old_load_id",id,"root_instance_id",root,"phase",phase,"old_load",old,"current_load_id",current,"current_load",current==null?null:loads.get(current),"new_load",fresh,"parent_intent",replacementParent("board_replacement_intent",intent),"parent_outcome",replacementParent("board_replacement_outcome",outcome),"intent_reconstructed_from_outcome",outcome!=null,"quarantined_by",quarantinedLoads.get(id),"binding_status",map("current_job_id",jobId,"replacement_job_selected",attempt.equals(jobId),"native_job_binding_retained",boundNativeJob!=null,"root_confirmation_retained",confirmed.contains(root),"recovered_from_journal",recovered));
        if(!chainIntents.isEmpty()){
            List<Object> chain=new ArrayList<>();for(int i=0;i<chainIntents.size();i++){Map<String,Object> step=chainIntents.get(i),done=continuationStepOutcomes.get(step.get("step_id"));String next=i+1<chainIntents.size()?(String)chainIntents.get(i+1).get("step_id"):null;chain.add(map("step_id",step.get("step_id"),"phase",done==null?"pending":"completed","new_load_id",((Map<?,?>)step.get("new_load")).get("load_id"),"parent_intent",replacementParent("board_continuation_intent",step),"parent_outcome",replacementParent("board_continuation_outcome",done),"superseded_by",next));}
            Map<String,Object> last=chainIntents.get(chainIntents.size()-1),done=continuationStepOutcomes.get(last.get("step_id"));row.putAll(map("continuation_chain",chain,"effective_phase",done==null?"pending":"completed","effective_new_load",(done==null?last:done).get("new_load"),"effective_step_id",last.get("step_id"),"effective_parent_intent",replacementParent("board_continuation_intent",last),"effective_parent_outcome",replacementParent("board_continuation_outcome",done)));
        }
        return NativeFaultedJobReplacement.frozen(row);
    }
    private Map<String,Object> currentContinuationRow(String operation,String digest,String attempt,String oldId)throws Exception {
        Map<String,Object> intent=null,outcome=null;for(Map<String,Object> row:replacementIntents.values())if(oldId.equals(row.get("old_load_id"))){if(intent!=null||!operation.equals(row.get("recovery_operation_id"))||!digest.equals(row.get("fault_set_sha256"))||!attempt.equals(row.get("new_job_id")))throw fault("BOARD_LOAD_RECORD","Foreign or duplicate original board intent");intent=row;}
        for(Map<String,Object> row:replacementReceipts.values())if(oldId.equals(row.get("old_load_id"))){if(outcome!=null||!operation.equals(row.get("recovery_operation_id"))||!digest.equals(row.get("fault_set_sha256"))||!attempt.equals(row.get("new_job_id")))throw fault("BOARD_LOAD_RECORD","Foreign or duplicate original board outcome");outcome=row;}return progressRow(operation,digest,attempt,oldId,intent,outcome);
    }
    private static String effectivePhase(Map<String,Object> row){return (String)row.getOrDefault("effective_phase",row.get("phase"));}
    private static Object effectiveParent(Map<String,Object> row,boolean outcome){String key=outcome?"parent_outcome":"parent_intent";return row.getOrDefault("effective_"+key,row.get(key));}
    private static Object effectiveNewLoad(Map<String,Object> row){return row.getOrDefault("effective_new_load",row.get("new_load"));}
    private static void indexReplacementProgress(Map<String,Object> row,String operation,String digest,String newJob,Set<String> required,Map<String,Map<String,Object>> index)throws Exception {
        if(!operation.equals(row.get("recovery_operation_id")))return;String old=(String)row.get("old_load_id");
        if(!digest.equals(row.get("fault_set_sha256"))||!newJob.equals(row.get("new_job_id"))||!required.contains(old)||index.put(old,row)!=null)throw fault("BOARD_LOAD_RECORD","Incomplete, repeated or foreign board replacement union");
    }
    private static Map<String,Object> replacementParent(String type,Map<String,Object> payload)throws Exception {
        return payload==null?null:map("type",type,"receipt_id",payload.get("receipt_id"),"payload_sha256",NativeFaultedJobReplacement.digest(payload),"payload",payload);
    }
    /** Observe the exact reconstructed graph under a fresh local restart decision. This stores
     * no selected Job, presence confirmation or executable board binding. */
    Map<String,Object> observeRestart(NativeFaultedJobReplacement.RestartPermit permit,Job oldJob,Job fresh,String attempt)throws Exception {
        restartNativeOwner();if(restartPublicationFault)throw fault("BOARD_RESTART_UNCERTAIN","Restart observation publication is uncertain; verify a new replay instance");
        Map<String,Object> authority=permit.authorizeBoards(this,oldJob,fresh,attempt);NativeFaultedJobReplacement.keys(authority,"reattachment_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","restart_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","old_boards","board_progress");
        Map<String,Object> captured=NativeFaultedJobReplacement.object(authority.get("old_boards")),progress=NativeFaultedJobReplacement.object(authority.get("board_progress"));
        List<Object> observed=restartNativeRoots(authority,oldJob,fresh,attempt);Map<String,Object> receipt=map("schema_version",1,"receipt_id",UUID.randomUUID().toString(),"old_boards_sha256",NativeFaultedJobReplacement.digest(captured),"board_progress_sha256",durableBoardProgressDigest(progress),"board_load_revision",revision(),"roots",observed,"machine_disabled",true,"nozzles_empty",true,"observation","fresh-native-simulator-on-owning-executor","binding_scope","continuation-only","original_outcomes_preserved",true,"execution_authority_restored",false,"physical_load_verified",false);
        for(String key:List.of("reattachment_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","restart_capture_sha256","original_recovery_operation_id","original_fault_set_sha256"))receipt.put(key,authority.get(key));
        Map<String,Object> retained=NativeFaultedJobReplacement.frozen(receipt);Runnable commit=prepareRestartObservation(retained);permit.check();
        try{sink.append("board_restart_observation",retained);commit.run();}
        catch(Exception|Error failure){restartPublicationFault=true;restartWitnesses.remove(attempt);refresh();throw failure;}
        permit.acceptForcedEvent("board_restart_observation",retained);permit.check();validateRestartFacts(retained);
        if(!NativeFaultedJobReplacement.same(observed,restartNativeRoots(authority,oldJob,fresh,attempt)))throw fault("BOARD_LOAD_HISTORY_MISMATCH","Restart native board observations changed during force");
        restartWitnesses.put(attempt,new RestartWitness(permit,oldJob,fresh,restartObservations.get(receipt.get("receipt_id"))));refresh();return restartObservations.get(receipt.get("receipt_id"));
    }
    Map<String,Object> restartObservationReceipt(String receiptId){return restartObservations.get(receiptId);}
    private static void restartNativeOwner()throws Exception {
        Configuration c=Configuration.get();if(c==null||c.getMachine()==null||!c.getMachine().isTask(Thread.currentThread()))throw fault("BOARD_RESTART_OWNER","Current native executor required");
        if(c.getMachine().isEnabled())throw fault("MACHINE_ENABLED","Disable before restart board observation");for(org.openpnp.spi.Head h:c.getMachine().getHeads())for(org.openpnp.spi.Nozzle n:h.getNozzles())if(n.getPart()!=null)throw fault("NOZZLE_OCCUPIED","Restart board observation requires empty native nozzles");
    }
    private static String durableBoardProgressDigest(Map<String,Object> progress)throws Exception {Map<String,Object> value=NativeFaultedJobReplacement.mutable(progress);for(Object row:(List<?>)value.get("rows"))NativeFaultedJobReplacement.object(row).remove("binding_status");return NativeFaultedJobReplacement.digest(value);}
    private List<Object> restartNativeRoots(Map<String,Object> authority,Job oldJob,Job fresh,String attempt)throws Exception {
        restartNativeOwner();requireDetachedReplacement(oldJob,fresh);Map<String,Root> originals=inspect(oldJob),next=inspect(fresh);Map<String,Object> captured=NativeFaultedJobReplacement.object(authority.get("old_boards")),progress=NativeFaultedJobReplacement.object(authority.get("board_progress"));Map<?,?> oldActive=(Map<?,?>)captured.get("active"),oldLoads=(Map<?,?>)captured.get("loads");
        List<String> ids=new ArrayList<>();for(Object id:oldActive.values())ids.add(NativeFaultedJobReplacement.uuid(id));Map<String,Object> current=replacementProgress((String)authority.get("original_recovery_operation_id"),(String)authority.get("original_fault_set_sha256"),attempt,ids);
        if(!sameDurableBoardProgress(progress,current)||!originals.keySet().equals(next.keySet())||!next.keySet().equals(oldActive.keySet()))throw fault("BOARD_RESTART_SCOPE","Restart native graph or complete original root union changed");
        List<Object> observed=new ArrayList<>();for(Object raw:(List<?>)progress.get("rows")){
            Map<String,Object> phase=NativeFaultedJobReplacement.object(raw),old=record(phase.get("old_load")),load=record(phase.get("current_load"));String id=(String)phase.get("old_load_id"),root=(String)phase.get("root_instance_id");Root original=originals.get(root),candidate=next.get(root);Map<String,Object> parent=effectiveParent(phase,!"pending".equals(effectivePhase(phase)))==null?null:NativeFaultedJobReplacement.object(effectiveParent(phase,!"pending".equals(effectivePhase(phase))));
            if(original==null||candidate==null||!Objects.equals(oldActive.get(root),id)||!NativeFaultedJobReplacement.same(oldLoads.get(id),old)||!original.signature.equals(old.get("signature"))||!candidate.signature.equals(load.get("signature"))||!candidate.location.getGlobalSide().name().equalsIgnoreCase((String)load.get("side")))throw fault("BOARD_RESTART_SCOPE","Restart native shape/side differs from exact durable root");
            Map<String,Object> oldHistory=history(oldJob,original),newHistory=history(fresh,candidate);if(historyRegression((Map<?,?>)old.get("placed_history"),oldHistory)!=null||!newHistory.isEmpty())throw fault("BOARD_LOAD_HISTORY_MISMATCH","Restart changes old history or imports placed candidate state");
            if(parent!=null&&!NativeFaultedJobReplacement.same(oldHistory,NativeFaultedJobReplacement.object(parent.get("payload")).get("old_native_history")))throw fault("BOARD_LOAD_HISTORY_MISMATCH","Restart original complete history differs from parent receipt");
            Map<String,Object> row=map("old_load_id",id,"root_instance_id",root,"phase",effectivePhase(phase),"phase_row_sha256",NativeFaultedJobReplacement.continuationPhaseDigest(phase),"old_load_sha256",NativeFaultedJobReplacement.digest(old),"current_load_id",load.get("load_id"),"current_load_sha256",NativeFaultedJobReplacement.digest(load),"parent_type",parent==null?null:parent.get("type"),"parent_receipt_id",parent==null?null:parent.get("receipt_id"),"parent_sha256",parent==null?null:parent.get("payload_sha256"),"old_native_history",oldHistory,"old_native_history_sha256",NativeFaultedJobReplacement.digest(oldHistory),"candidate_native_history_sha256",NativeFaultedJobReplacement.digest(newHistory),"native_original_model_sha256",original.signature,"native_candidate_model_sha256",candidate.signature,"side",candidate.location.getGlobalSide().name().toLowerCase(Locale.ROOT));observed.add(row);
        }return observed;
    }
    private Runnable prepareRestartObservation(Map<String,Object> supplied)throws Exception {
        Map<String,Object> p=NativeFaultedJobReplacement.mutable(supplied);for(Object raw:(List<?>)p.get("roots")){Map<String,Object> row=NativeFaultedJobReplacement.object(raw);for(String key:List.of("parent_type","parent_receipt_id","parent_sha256"))row.putIfAbsent(key,null);}
        NativeFaultedJobReplacement.keys(p,"schema_version","receipt_id","reattachment_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","restart_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","old_boards_sha256","board_progress_sha256","board_load_revision","roots","machine_disabled","nozzles_empty","observation","binding_scope","original_outcomes_preserved","execution_authority_restored","physical_load_verified");
        if(NativeFaultedJobReplacement.number(p.get("schema_version"))!=1||!"continuation-only".equals(p.get("binding_scope"))||!"fresh-native-simulator-on-owning-executor".equals(p.get("observation"))||!Boolean.TRUE.equals(p.get("machine_disabled"))||!Boolean.TRUE.equals(p.get("nozzles_empty"))||!Boolean.TRUE.equals(p.get("original_outcomes_preserved"))||!Boolean.FALSE.equals(p.get("execution_authority_restored"))||!Boolean.FALSE.equals(p.get("physical_load_verified")))throw fault("BOARD_LOAD_RECORD","Restart observation cannot restore board execution authority");
        for(String key:List.of("receipt_id","reattachment_id","replacement_attempt_id","recovery_operation_id","original_recovery_operation_id"))NativeFaultedJobReplacement.uuid(p.get(key));for(String key:List.of("fault_set_sha256","restart_capture_sha256","original_fault_set_sha256","old_boards_sha256","board_progress_sha256"))NativeFaultedJobReplacement.hash(p.get(key));
        String receipt=(String)p.get("receipt_id");if(restartObservations.size()>=4096||restartObservations.containsKey(receipt)||replacementReceipts.containsKey(receipt)||replacementIntents.containsKey(receipt)||continuationStepIntents.containsKey(receipt)||continuationAdoptions.containsKey(receipt)||pending.containsKey(receipt))throw fault("BOARD_RESTART_REPEATED","Restart observation capacity or identity conflict");
        for(Map<String,Object> previous:restartObservations.values())if(Objects.equals(previous.get("reattachment_id"),p.get("reattachment_id")))throw fault("BOARD_RESTART_REPEATED","This restart decision already observed the full board union");
        validateRestartFacts(p);Map<String,Object> retained=NativeFaultedJobReplacement.frozen(p);return ()->{restartObservations.put(receipt,retained);refresh();};
    }
    private void validateRestartFacts(Map<String,Object> p)throws Exception {
        if(!pending.isEmpty()||!revision().equals(p.get("board_load_revision")))throw fault("BOARD_RESTART_SCOPE","Ordinary board loading or revision changed");
        if(!(p.get("roots") instanceof List)||((List<?>)p.get("roots")).isEmpty()||((List<?>)p.get("roots")).size()>100)throw fault("BOARD_LOAD_RECORD","Bounded complete restart root union required");
        List<String> ids=new ArrayList<>();Set<String> rootsSeen=new HashSet<>();for(Object raw:(List<?>)p.get("roots")){Map<String,Object> row=NativeFaultedJobReplacement.object(raw);String id=NativeFaultedJobReplacement.uuid(row.get("old_load_id"));if(ids.contains(id)||!rootsSeen.add((String)row.get("root_instance_id")))throw fault("BOARD_LOAD_RECORD","Repeated restart root/load");ids.add(id);}
        Map<String,Object> progress=replacementProgress((String)p.get("original_recovery_operation_id"),(String)p.get("original_fault_set_sha256"),(String)p.get("replacement_attempt_id"),ids);if(!durableBoardProgressDigest(progress).equals(p.get("board_progress_sha256")))throw fault("BOARD_RESTART_SCOPE","Restart full board progress changed");
        Map<String,Map<String,Object>> phases=new HashMap<>();for(Object raw:(List<?>)progress.get("rows")){Map<String,Object> row=NativeFaultedJobReplacement.object(raw);phases.put((String)row.get("old_load_id"),row);}
        for(Object raw:(List<?>)p.get("roots")){
            Map<String,Object> row=NativeFaultedJobReplacement.object(raw);NativeFaultedJobReplacement.keys(row,"old_load_id","root_instance_id","phase","phase_row_sha256","old_load_sha256","current_load_id","current_load_sha256","parent_type","parent_receipt_id","parent_sha256","old_native_history","old_native_history_sha256","candidate_native_history_sha256","native_original_model_sha256","native_candidate_model_sha256","side");
            Map<String,Object> phase=phases.get(row.get("old_load_id")),old=record(phase.get("old_load")),current=record(phase.get("current_load"));String kind=effectivePhase(phase);Map<String,Object> parent=kind.equals("untouched")?null:NativeFaultedJobReplacement.object(effectiveParent(phase,kind.equals("completed")));
            if(!Objects.equals(row.get("root_instance_id"),phase.get("root_instance_id"))||!Objects.equals(row.get("phase"),kind)||!NativeFaultedJobReplacement.continuationPhaseDigest(phase).equals(row.get("phase_row_sha256"))||!NativeFaultedJobReplacement.digest(old).equals(row.get("old_load_sha256"))||!Objects.equals(row.get("current_load_id"),phase.get("current_load_id"))||!NativeFaultedJobReplacement.digest(current).equals(row.get("current_load_sha256"))||!Objects.equals(active.get(row.get("root_instance_id")),row.get("current_load_id")))throw fault("BOARD_RESTART_SCOPE","Restart root changed exact durable phase or identity");
            if(!Objects.equals(parent==null?null:parent.get("type"),row.get("parent_type"))||!Objects.equals(parent==null?null:parent.get("receipt_id"),row.get("parent_receipt_id"))||!Objects.equals(parent==null?null:parent.get("payload_sha256"),row.get("parent_sha256")))throw fault("BOARD_RESTART_SCOPE","Restart root parent differs");
            Map<String,Object> oldHistory=NativeFaultedJobReplacement.object(row.get("old_native_history"));for(Object value:oldHistory.values())if(!(value instanceof Boolean))throw fault("BOARD_LOAD_RECORD","Complete native history must retain Boolean values");
            if(!NativeFaultedJobReplacement.digest(oldHistory).equals(row.get("old_native_history_sha256"))||historyRegression((Map<?,?>)old.get("placed_history"),oldHistory)!=null||!NativeFaultedJobReplacement.digest(Map.of()).equals(row.get("candidate_native_history_sha256"))||!Objects.equals(old.get("signature"),row.get("native_original_model_sha256"))||!Objects.equals(current.get("signature"),row.get("native_candidate_model_sha256"))||!Objects.equals(current.get("side"),row.get("side")))throw fault("BOARD_LOAD_HISTORY_MISMATCH","Restart native observation changes model, side or history");
            if(kind.equals("untouched")){if(!Objects.equals(row.get("old_load_id"),row.get("current_load_id"))||quarantinedLoads.containsKey(row.get("old_load_id"))||!"loaded".equals(current.get("state")))throw fault("BOARD_RESTART_SCOPE","Untouched board is not its exact retained active load");}
            else {Map<String,Object> payload=NativeFaultedJobReplacement.object(parent.get("payload"));if(!NativeFaultedJobReplacement.same(current,payload.get("new_load"))||!NativeFaultedJobReplacement.same(oldHistory,payload.get("old_native_history"))||!Objects.equals(quarantinedLoads.get(row.get("old_load_id")),originalQuarantine((String)parent.get("type"),payload))||!((Map<?,?>)current.get("placed_history")).isEmpty()||!(kind.equals("pending")?"loading_unknown":"loaded").equals(current.get("state")))throw fault("BOARD_LOAD_HISTORY_MISMATCH","Restart target has no exact untouched completed/unknown receipt");}
        }
    }
    /** Called only after independent continuation admission. A stored receipt alone is useless. */
    private void requireRestartForContinuation(NativeFaultedJobReplacement.ContinuationPermit permit,Job oldJob,Job fresh,String attempt)throws Exception {
        permit.check();RestartWitness witness=restartWitnesses.get(attempt);
        if(witness==null){if(quarantinedNativeJobs.contains(oldJob)||!recovered&&boundNativeJob==oldJob)return;throw fault("BOARD_RESTART_REQUIRED","Fresh sealed board observations are required for reconstructed jobs");}
        if(restartPublicationFault||witness.oldJob!=oldJob||witness.freshJob!=fresh)throw fault("BOARD_RESTART_SCOPE","Restart native graph identity changed");
        witness.permit.requireSealedBoards(this,oldJob,fresh,attempt,(String)witness.receipt.get("receipt_id"));requireDetachedReplacement(oldJob,fresh);permit.check();quarantinedNativeJobs.add(oldJob);
    }
    List<Map<String,Object>> quarantineAndBindReplacement(NativeFaultedJobReplacement.Permit permit,Job oldJob,Job fresh,String newJobId)throws Exception {
        Map<String,Object> authority=permit.authorizeBoards(this,oldJob,fresh,newJobId),captured=NativeFaultedJobReplacement.object(authority.get("old_boards"));
        if(!NativeFaultedJobReplacement.same(captured,captureReplacementState((String)captured.get("job_id"))))throw fault("BOARD_LOAD_REVISION_CONFLICT","Faulted board dependencies changed");
        requireDetachedReplacement(oldJob,fresh);if(!pending.isEmpty()||!replacementIntents.isEmpty())throw fault("BOARD_LOAD_UNKNOWN","Unresolved board change requires explicit document recovery");
        Map<String,Root> originals=inspect(oldJob),next=inspect(fresh);if(loads.size()+next.size()>MAX_LOADS)throw fault("BOARD_LOAD_CAPACITY","Retained replacement capacity reached");
        List<Map<String,Object>> receipts=new ArrayList<>();String nextRevision=signature(next.values().stream().map(r->r.signature).toArray());
        for(Root root:next.values()){
            permit.check();String priorId=(String)((Map<?,?>)captured.get("active")).get(root.id);Map<String,Object> prior=loads.get(priorId),nativeHistory=history(oldJob,originals.get(root.id));
            if(historyRegression((Map<?,?>)prior.get("placed_history"),nativeHistory)!=null)throw fault("BOARD_LOAD_HISTORY_MISMATCH","Original native history regressed before quarantine");
            Map<String,Object> record=newLoad(root);record.putAll(map("state","loading_unknown","side",root.location.getGlobalSide().name().toLowerCase(Locale.ROOT),"job_id",newJobId,"job_revision",nextRevision,"authority","native-simulator","physical_load_verified",false,"simulator_origin",true,"change_action","faulted-attempt-replacement","initial_fixture_registration",false,"registration","invalidated","load_revision","load-"+(revision+1),"updated_at",Instant.now().toString()));
            String receipt=UUID.randomUUID().toString();Map<String,Object> intent=map("schema_version",1,"receipt_id",receipt,"recovery_operation_id",authority.get("recovery_operation_id"),"fault_set_sha256",authority.get("fault_set_sha256"),"revision",revision+1,"old_load_id",priorId,"old_load",prior,"old_native_history",nativeHistory,"new_load",record,"old_job_id",captured.get("job_id"),"new_job_id",newJobId,"old_disposition","quarantined-uncertain-attempt","physical_load_verified",false);
            appendReplacement("board_replacement_intent",intent);quarantinedNativeJobs.add(oldJob);permit.check();
            // Only new objects are bound. Never call removePlacedStatus or a setter on oldJob.
            if(!fresh.getPlacedStatusSnapshot().isEmpty()||!replacementModel(oldJob).equals(replacementModel(fresh)))throw fault("BOARD_REPLACEMENT_NOT_FRESH","Replacement changed during durable admission");
            Map<String,Object> outcome=NativeFaultedJobReplacement.mutable(intent);NativeFaultedJobReplacement.object(outcome.get("new_load")).put("state","loaded");appendReplacement("board_replacement_outcome",outcome);receipts.add(replacementReceipts.get(receipt));
        }
        continuationBinding=null;boundNativeJob=fresh;jobId=newJobId;jobRevision=nextRevision;roots=next;confirmed.clear();confirmed.addAll(next.keySet());recovered=false;refresh();return Collections.unmodifiableList(receipts);
    }
    /** Bind only the already completed effective board union under a forced outer publication
     * intent. No load, history, registration or journal record is created here. */
    void bindContinuedReplacement(NativeFaultedJobReplacement.Publication publication,Job oldJob,Job fresh,String attempt)throws Exception {
        try {
            if(publication==null)throw fault("BOARD_CONTINUATION_BINDING","Exact publication capability required");
            publication.check();Map<String,Object> authority=publication.authorizeBoards(this,oldJob,fresh,attempt);Map<String,Root> next=validateContinuedRoots(authority,oldJob,fresh,attempt);
            if(continuationBinding!=null&&continuationBinding.publication==publication)throw fault("BOARD_CONTINUATION_BINDING","This publication already staged its board binding");
            Map<String,String> ids=new LinkedHashMap<>();for(String root:next.keySet())ids.put(root,active.get(root));
            ContinuationBinding staged=new ContinuationBinding(publication,oldJob,fresh,attempt,NativeFaultedJobReplacement.frozen(authority),ids,next);
            publication.check();continuationBinding=staged;boundNativeJob=fresh;jobId=attempt;jobRevision=signature(next.values().stream().map(r->r.signature).toArray());roots=next;
            confirmed.clear();confirmed.addAll(next.keySet());for(String id:ids.values())restartUnconfirmedLoads.remove(id);recovered=restartConfirmationRequired();refresh();
            requireContinuationBinding(publication,fresh,attempt);
        }catch(Exception|Error failure){confirmed.clear();refresh();throw failure;}
    }
    /** Check staging before the outer publication outcome. This deliberately does not use
     * bindingReady, which becomes true only after that outcome and native job authority agree. */
    void requireContinuationBinding(NativeFaultedJobReplacement.Publication publication,Job fresh,String attempt)throws Exception {
        try {
            if(publication==null)throw fault("BOARD_CONTINUATION_BINDING","Exact publication capability required");
            publication.check();ContinuationBinding staged=continuationBinding;
            if(staged==null||staged.publication!=publication||staged.freshJob!=fresh||!staged.attempt.equals(attempt))throw fault("BOARD_CONTINUATION_BINDING","Exact staged continuation binding is absent");
            Map<String,Object> authority=publication.authorizeBoards(this,staged.oldJob,fresh,attempt);
            for(String key:List.of("publication_id","continuation_id","replacement_attempt_id","continuation_outcome_sha256"))if(!Objects.equals(authority.get(key),staged.authority.get(key)))throw fault("BOARD_CONTINUATION_BINDING","Staged publication identity changed");
            Map<String,Root> actual=validateContinuedRoots(authority,staged.oldJob,fresh,attempt);requireStagedIdentity(staged,fresh,attempt,actual);publication.check();
        }catch(Exception|Error failure){confirmed.clear();refresh();throw failure;}
    }
    private boolean requiresContinuationPublication(String attempt){
        for(Map<String,Object> row:continuationAdoptions.values())if(Objects.equals(attempt,row.get("new_job_id")))return true;
        for(Map<String,Object> row:continuationStepIntents.values())if(Objects.equals(attempt,row.get("new_job_id")))return true;return false;
    }
    private Map<String,Root> validateContinuedRoots(Map<String,Object> authority,Job oldJob,Job fresh,String attempt)throws Exception {
        NativeFaultedJobReplacement.uuid(attempt);for(String key:List.of("publication_id","continuation_id","replacement_attempt_id"))NativeFaultedJobReplacement.uuid(authority.get(key));NativeFaultedJobReplacement.hash(authority.get("continuation_outcome_sha256"));
        if(!attempt.equals(authority.get("replacement_attempt_id"))||continuationPublicationFault||!pending.isEmpty()||hasCurrentUnresolvedReplacementIntents()||quarantinedNativeJobs.contains(fresh)||quarantinedJobs.containsKey(attempt))throw fault("BOARD_CONTINUATION_BINDING","Continuation board publication is foreign or unresolved");
        requireDetachedReplacement(oldJob,fresh);Map<String,Root> originals=inspect(oldJob),next=inspect(fresh);
        Map<String,Object> captured=NativeFaultedJobReplacement.object(authority.get("old_boards")),progress=NativeFaultedJobReplacement.object(authority.get("board_progress"));Map<?,?> oldActive=(Map<?,?>)captured.get("active"),oldLoads=(Map<?,?>)captured.get("loads");
        if(!originals.keySet().equals(next.keySet())||!next.keySet().equals(oldActive.keySet())||!Objects.equals(progress.get("replacement_job_id"),attempt))throw fault("BOARD_CONTINUATION_BINDING","Publication changes the exact original native root union");
        List<String> oldIds=new ArrayList<>();for(Object value:oldActive.values())oldIds.add(NativeFaultedJobReplacement.uuid(value));
        Map<String,Object> current=replacementProgress((String)progress.get("recovery_operation_id"),(String)progress.get("fault_set_sha256"),attempt,oldIds);
        if(!sameDurableBoardProgress(current,progress))throw fault("BOARD_CONTINUATION_BINDING","Board phases changed after the sealed continuation");
        Set<String> rootsSeen=new HashSet<>(),loadsSeen=new HashSet<>();String expectedRevision=signature(next.values().stream().map(r->r.signature).toArray());
        for(Object raw:(List<?>)progress.get("rows")){
            Map<String,Object> phase=NativeFaultedJobReplacement.object(raw);String root=(String)phase.get("root_instance_id"),oldId=(String)phase.get("old_load_id");
            if(!rootsSeen.add(root)||!next.containsKey(root)||!"completed".equals(effectivePhase(phase)))throw fault("BOARD_CONTINUATION_BINDING","Every original root requires one completed effective load");
            Map<String,Object> link=NativeFaultedJobReplacement.object(effectiveParent(phase,true));String type=(String)link.get("type"),parentId=NativeFaultedJobReplacement.uuid(link.get("receipt_id"));Map<String,Object> parent=completedContinuationParent(type,parentId);
            if(parent==null||!NativeFaultedJobReplacement.same(parent,link.get("payload"))||!NativeFaultedJobReplacement.digest(parent).equals(link.get("payload_sha256")))throw fault("BOARD_CONTINUATION_BINDING","Effective board parent receipt is absent or changed");
            Map<String,Object> old=record(parent.get("old_load")),load=record(parent.get("new_load"));String loadId=(String)load.get("load_id");
            if(!loadsSeen.add(loadId)||!Objects.equals(oldActive.get(root),oldId)||!Objects.equals(captured.get("job_id"),parent.get("old_job_id"))||!Objects.equals(attempt,parent.get("new_job_id"))||!NativeFaultedJobReplacement.same(oldLoads.get(oldId),old)||!NativeFaultedJobReplacement.same(loads.get(oldId),old)||!NativeFaultedJobReplacement.same(phase.get("old_load"),old)||!NativeFaultedJobReplacement.same(effectiveNewLoad(phase),load)||!NativeFaultedJobReplacement.same(loads.get(loadId),load)||!NativeFaultedJobReplacement.same(phase.get("current_load"),load)||!Objects.equals(phase.get("current_load_id"),loadId)||!Objects.equals(active.get(root),loadId)||!Objects.equals(quarantinedLoads.get(oldId),originalQuarantine(type,parent))||!Objects.equals(phase.get("quarantined_by"),quarantinedLoads.get(oldId))||quarantinedLoads.containsKey(loadId)||!"loaded".equals(load.get("state"))||!attempt.equals(load.get("job_id"))||!expectedRevision.equals(load.get("job_revision"))||!((Map<?,?>)load.get("placed_history")).isEmpty())throw fault("BOARD_CONTINUATION_BINDING","Completed current board identity, quarantine or original history changed");
            requireContinuationNative(oldJob,fresh,root,parent);
        }
        if(!rootsSeen.equals(next.keySet())||!fresh.getPlacedStatusSnapshot().isEmpty())throw fault("BOARD_CONTINUATION_BINDING","Publication omits a native root or carries new placed history");return next;
    }
    private static boolean sameDurableBoardProgress(Map<String,Object> a,Map<String,Object> b)throws Exception {
        Map<String,Object> left=NativeFaultedJobReplacement.mutable(a),right=NativeFaultedJobReplacement.mutable(b);
        for(Map<String,Object> progress:List.of(left,right))for(Object raw:(List<?>)progress.get("rows"))NativeFaultedJobReplacement.object(raw).remove("binding_status");return NativeFaultedJobReplacement.same(left,right);
    }
    private void requireStagedIdentity(ContinuationBinding staged,Job fresh,String attempt,Map<String,Root> actual)throws Exception {
        if(boundNativeJob!=fresh||staged.freshJob!=fresh||!Objects.equals(jobId,attempt)||!staged.attempt.equals(attempt)||!actual.keySet().equals(staged.nativeRoots.keySet())||!roots.keySet().equals(actual.keySet())||!confirmed.equals(actual.keySet()))throw fault("BOARD_CONTINUATION_BINDING","Staged native job/root identity or confirmations changed");
        for(String id:actual.keySet()){
            Root now=actual.get(id),prior=staged.nativeRoots.get(id);if(now.location!=prior.location||roots.get(id).location!=prior.location||now.boards.size()!=prior.boards.size()||!Objects.equals(active.get(id),staged.loadIds.get(id)))throw fault("BOARD_CONTINUATION_BINDING","Staged native root or active load was replaced");
            for(int index=0;index<now.boards.size();index++)if(now.boards.get(index)!=prior.boards.get(index))throw fault("BOARD_CONTINUATION_BINDING","Staged native descendant identity changed");
        }
    }
    /** Receipt-only adoption of one completed root under an exact new local continuation permit.
     * A mixed batch may still have pending/untouched roots; this method does not bind a Job,
     * confirm any root, create a load, or change native history or registration. */
    Map<String,Object> adoptCompletedReplacement(NativeFaultedJobReplacement.ContinuationPermit permit,Job oldJob,Job fresh,String replacementJobId,String oldLoadId)throws Exception {
        if(continuationPublicationFault)throw fault("BOARD_CONTINUATION_FAULT","Uncertain adoption force requires verified replay");
        Map<String,Object> authority=permit.authorizeBoards(this,oldJob,fresh,replacementJobId),captured=NativeFaultedJobReplacement.object(authority.get("old_boards")),progress=NativeFaultedJobReplacement.object(authority.get("board_progress"));
        requireRestartForContinuation(permit,oldJob,fresh,replacementJobId);NativeFaultedJobReplacement.uuid(oldLoadId);Map<String,Object> phase=null;
        for(Object raw:(List<?>)progress.get("rows")){Map<String,Object> row=NativeFaultedJobReplacement.object(raw);if(oldLoadId.equals(row.get("old_load_id"))){if(phase!=null)throw fault("BOARD_LOAD_RECORD","Repeated continuation root");phase=row;}}
        if(phase==null||!"completed".equals(effectivePhase(phase)))throw fault("BOARD_CONTINUATION_INCOMPLETE","Only an exact completed board root can be adopted");
        Map<String,Object> parentLink=NativeFaultedJobReplacement.object(effectiveParent(phase,true));String parentId=NativeFaultedJobReplacement.uuid(parentLink.get("receipt_id")),parentType=(String)parentLink.get("type");Map<String,Object> parent=completedContinuationParent(parentType,parentId);
        if(parent==null||!NativeFaultedJobReplacement.same(parent,parentLink.get("payload"))||!NativeFaultedJobReplacement.digest(parent).equals(parentLink.get("payload_sha256")))throw fault("BOARD_LOAD_RECORD","Completed board parent receipt differs");
        Map<String,Object> old=record(parent.get("old_load")),next=record(parent.get("new_load"));String root=(String)old.get("root_instance_id");
        if(!Objects.equals(captured.get("job_id"),parent.get("old_job_id"))||!Objects.equals(((Map<?,?>)captured.get("active")).get(root),oldLoadId)||!NativeFaultedJobReplacement.same(((Map<?,?>)captured.get("loads")).get(oldLoadId),old)||!NativeFaultedJobReplacement.same(phase.get("old_load"),old)||!NativeFaultedJobReplacement.same(effectiveNewLoad(phase),next)||!NativeFaultedJobReplacement.same(phase.get("current_load"),next)||!Objects.equals(phase.get("current_load_id"),next.get("load_id"))||!Objects.equals(phase.get("quarantined_by"),originalQuarantine(parentType,parent)))throw fault("BOARD_LOAD_HISTORY_MISMATCH","Captured board dependency differs from exact parent");
        // Permit validation checks retained native object identities. Repeat the native root and
        // complete-history checks here before and after force; replay never performs them.
        requireContinuationNative(oldJob,fresh,root,parent);
        Map<String,Object> receipt=map("schema_version",2,"parent_receipt_type",parentType,"receipt_id",UUID.randomUUID().toString(),"continuation_id",authority.get("continuation_id"),"replacement_attempt_id",authority.get("replacement_attempt_id"),"recovery_operation_id",authority.get("recovery_operation_id"),"fault_set_sha256",authority.get("fault_set_sha256"),"continuation_capture_sha256",authority.get("continuation_capture_sha256"),"original_recovery_operation_id",authority.get("original_recovery_operation_id"),"original_fault_set_sha256",authority.get("original_fault_set_sha256"),"phase_row_sha256",NativeFaultedJobReplacement.continuationPhaseDigest(phase),"parent_receipt_id",parentId,"parent_receipt_sha256",NativeFaultedJobReplacement.digest(parent),"old_load_id",oldLoadId,"new_load_id",next.get("load_id"),"old_job_id",parent.get("old_job_id"),"new_job_id",replacementJobId,"root_instance_id",root,"board_load_revision",revision(),"old_native_history_sha256",NativeFaultedJobReplacement.digest(NativeFaultedJobReplacement.object(parent.get("old_native_history"))),"new_native_root_sha256",next.get("signature"),"adoption_scope","completed-same-process","execution_authority_restored",false,"physical_load_verified",false);
        Map<String,Object> frozen=NativeFaultedJobReplacement.frozen(receipt);Runnable commit=prepareContinuationAdoption(frozen);permit.check();
        try{sink.append("board_continuation_adoption",frozen);commit.run();permit.check();requireContinuationNative(oldJob,fresh,root,parent);}catch(Exception|Error failure){continuationPublicationFault=true;refresh();throw failure;}
        return continuationAdoptions.get((String)receipt.get("receipt_id"));
    }
    /** Convenience for the completed subset only. Pending/untouched roots are never relabeled. */
    List<Map<String,Object>> adoptCompletedReplacement(NativeFaultedJobReplacement.ContinuationPermit permit,Job oldJob,Job fresh,String replacementJobId)throws Exception {
        Map<String,Object> authority=permit.authorizeBoards(this,oldJob,fresh,replacementJobId),progress=NativeFaultedJobReplacement.object(authority.get("board_progress"));List<Map<String,Object>> receipts=new ArrayList<>();
        for(Object raw:(List<?>)progress.get("rows")){Map<String,Object> row=NativeFaultedJobReplacement.object(raw);if("completed".equals(effectivePhase(row)))receipts.add(adoptCompletedReplacement(permit,oldJob,fresh,replacementJobId,(String)row.get("old_load_id")));}
        return Collections.unmodifiableList(receipts);
    }
    Map<String,Object> continuationAdoptionReceipt(String receiptId){return continuationAdoptions.get(receiptId);}
    List<Map<String,Object>> continuationAdoptionSnapshot(){return Collections.unmodifiableList(new ArrayList<>(continuationAdoptions.values()));}
    private void requireContinuationNative(Job oldJob,Job fresh,String rootId,Map<String,Object> parent)throws Exception {
        if(!quarantinedNativeJobs.contains(oldJob))throw fault("BOARD_CONTINUATION_BINDING","Original same-process native quarantine is unavailable");
        requireDetachedReplacement(oldJob,fresh);Map<String,Root> oldRoots=inspect(oldJob),freshRoots=inspect(fresh);Root old=oldRoots.get(rootId),next=freshRoots.get(rootId);
        Map<String,Object> load=record(parent.get("new_load"));
        if(old==null||next==null||!next.signature.equals(load.get("signature"))||!next.location.getGlobalSide().name().equalsIgnoreCase((String)load.get("side"))||!NativeFaultedJobReplacement.same(boards(next,(Map<?,?>)load.get("board_ids")),load.get("boards"))||!NativeFaultedJobReplacement.same(history(oldJob,old),parent.get("old_native_history"))||!NativeFaultedJobReplacement.same(history(fresh,next),load.get("placed_history")))throw fault("BOARD_LOAD_HISTORY_MISMATCH","Current native root or complete history differs from completed replacement");
    }
    private Runnable prepareContinuationAdoption(Map<String,Object> supplied)throws Exception {
        Map<String,Object> p=NativeFaultedJobReplacement.frozen(supplied);long schema=NativeFaultedJobReplacement.number(p.get("schema_version"));List<String> fields=new ArrayList<>(List.of("schema_version","receipt_id","continuation_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","continuation_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","phase_row_sha256","parent_receipt_id","parent_receipt_sha256","old_load_id","new_load_id","old_job_id","new_job_id","root_instance_id","board_load_revision","old_native_history_sha256","new_native_root_sha256","adoption_scope","execution_authority_restored","physical_load_verified"));if(schema==2)fields.add("parent_receipt_type");NativeFaultedJobReplacement.keys(p,fields.toArray(String[]::new));
        if((schema!=1&&schema!=2)||!"completed-same-process".equals(p.get("adoption_scope"))||!Boolean.FALSE.equals(p.get("execution_authority_restored"))||!Boolean.FALSE.equals(p.get("physical_load_verified")))throw fault("BOARD_LOAD_RECORD","Invalid continuation adoption scope");
        for(String key:List.of("receipt_id","continuation_id","replacement_attempt_id","recovery_operation_id","original_recovery_operation_id","parent_receipt_id","old_load_id","new_load_id","old_job_id","new_job_id"))NativeFaultedJobReplacement.uuid(p.get(key));
        for(String key:List.of("fault_set_sha256","continuation_capture_sha256","original_fault_set_sha256","phase_row_sha256","parent_receipt_sha256","old_native_history_sha256","new_native_root_sha256"))NativeFaultedJobReplacement.hash(p.get(key));
        String receipt=(String)p.get("receipt_id"),parentId=(String)p.get("parent_receipt_id"),parentType=schema==1?"board_replacement_outcome":(String)p.get("parent_receipt_type");Map<String,Object> parent=completedContinuationParent(parentType,parentId);
        if(parent==null||!NativeFaultedJobReplacement.digest(parent).equals(p.get("parent_receipt_sha256"))||Objects.equals(p.get("recovery_operation_id"),p.get("original_recovery_operation_id"))||!Objects.equals(p.get("original_recovery_operation_id"),parent.get(parentType.equals("board_replacement_outcome")?"recovery_operation_id":"original_recovery_operation_id"))||!Objects.equals(p.get("original_fault_set_sha256"),parent.get(parentType.equals("board_replacement_outcome")?"fault_set_sha256":"original_fault_set_sha256"))||!Objects.equals(p.get("replacement_attempt_id"),p.get("new_job_id"))||!Objects.equals(p.get("new_job_id"),parent.get("new_job_id"))||!Objects.equals(p.get("old_job_id"),parent.get("old_job_id"))||!Objects.equals(p.get("old_load_id"),parent.get("old_load_id")))throw fault("BOARD_LOAD_RECORD","Continuation adoption has no exact original parent");
        Map<String,Object> old=record(parent.get("old_load")),fresh=record(parent.get("new_load"));String root=(String)old.get("root_instance_id");
        if(!Objects.equals(p.get("root_instance_id"),root)||!Objects.equals(p.get("new_load_id"),fresh.get("load_id"))||!Objects.equals(p.get("new_native_root_sha256"),fresh.get("signature"))||!NativeFaultedJobReplacement.digest(NativeFaultedJobReplacement.object(parent.get("old_native_history"))).equals(p.get("old_native_history_sha256"))||!Objects.equals(p.get("board_load_revision"),revision())||!Objects.equals(quarantinedLoads.get(p.get("old_load_id")),originalQuarantine(parentType,parent))||!Objects.equals(active.get(root),fresh.get("load_id"))||!NativeFaultedJobReplacement.same(loads.get(p.get("old_load_id")),old)||!NativeFaultedJobReplacement.same(loads.get(fresh.get("load_id")),fresh)||replacementIntents.containsKey(parentId))throw fault("BOARD_LOAD_RECORD","Completed board load or quarantine changed before adoption");
        if(continuationAdoptions.size()>=4096||continuationAdoptions.containsKey(receipt)||replacementReceipts.containsKey(receipt)||replacementIntents.containsKey(receipt)||continuationStepIntents.containsKey(receipt))throw fault("BOARD_LOAD_CAPACITY","Continuation receipt capacity or identity conflict");
        for(Map<String,Object> previous:continuationAdoptions.values())if(Objects.equals(previous.get("continuation_id"),p.get("continuation_id"))&&Objects.equals(previous.get("parent_receipt_id"),parentId))throw fault("BOARD_LOAD_RECORD","Completed root already adopted by this continuation");
        return ()->{continuationAdoptions.put(receipt,p);refresh();};
    }
    private Map<String,Object> completedContinuationParent(String type,String id){return "board_replacement_outcome".equals(type)?replacementReceipts.get(id):"board_continuation_outcome".equals(type)?continuationStepOutcomes.get(id):null;}
    private static Object originalQuarantine(String type,Map<String,Object> parent){return Set.of("board_replacement_intent","board_replacement_outcome").contains(type)?parent.get("receipt_id"):parent.get("original_quarantined_by")==null?parent.get("step_id"):parent.get("original_quarantined_by");}
    Map<String,Object> continuationStepIntent(String id){return continuationStepIntents.get(id);}
    Map<String,Object> continuationStepOutcome(String id){return continuationStepOutcomes.get(id);}
    List<Map<String,Object>> unresolvedContinuationIntents(){List<Map<String,Object>> out=new ArrayList<>();for(Map<String,Object> row:continuationStepIntents.values()){Map<?,?> next=(Map<?,?>)row.get("new_load");if(!continuationStepOutcomes.containsKey(row.get("step_id"))&&Objects.equals(active.get(next.get("root_instance_id")),next.get("load_id")))out.add(row);}return Collections.unmodifiableList(out);}
    boolean hasCurrentUnresolvedReplacementIntents(){if(!unresolvedContinuationIntents().isEmpty())return true;for(Map<String,Object> row:replacementIntents.values()){Map<?,?> next=(Map<?,?>)row.get("new_load");if(Objects.equals(active.get(next.get("root_instance_id")),next.get("load_id")))return true;}return false;}
    /** Explicitly replace an untouched root or supersede its unknown candidate load. Every
     * decision allocates further new load/board identities; old facts remain retained verbatim.
     * The detached native Job already exists. No setter/history reset/binding/confirmation occurs. */
    Map<String,Object> continueReplacement(NativeFaultedJobReplacement.ContinuationPermit permit,Job oldJob,Job fresh,String replacementJobId,String originalOldLoadId)throws Exception {
        if(continuationPublicationFault)throw fault("BOARD_CONTINUATION_FAULT","Uncertain continuation publication requires verified replay");
        try(var step=permit.beginBoardStep(this,oldJob,fresh,replacementJobId,originalOldLoadId)){
            Map<String,Object> authority=step.authority(),phase=NativeFaultedJobReplacement.object(authority.get("phase_row")),captured=NativeFaultedJobReplacement.object(authority.get("old_boards"));requireRestartForContinuation(permit,oldJob,fresh,replacementJobId);String effective=effectivePhase(phase);
            if(!Set.of("untouched","pending").contains(effective))throw fault("BOARD_CONTINUATION_SCOPE","Completed roots require adoption, not another board replacement");
            requireDetachedReplacement(oldJob,fresh);Map<String,Root> originals=inspect(oldJob),next=inspect(fresh);String root=(String)phase.get("root_instance_id");Root oldRoot=originals.get(root),newRoot=next.get(root);
            Map<String,Object> old=record(phase.get("old_load")),previous=record(phase.get("current_load"));
            if(oldRoot==null||newRoot==null||!Objects.equals(originalOldLoadId,old.get("load_id"))||!Objects.equals(captured.get("job_id"),old.get("job_id"))||!Objects.equals(((Map<?,?>)captured.get("active")).get(root),originalOldLoadId)||!NativeFaultedJobReplacement.same(((Map<?,?>)captured.get("loads")).get(originalOldLoadId),old))throw fault("BOARD_LOAD_HISTORY_MISMATCH","Continuation is outside original captured native root");
            Map<String,Object> parent=effectiveParent(phase,false)==null?null:NativeFaultedJobReplacement.object(effectiveParent(phase,false));Map<String,Object> record=newLoad(newRoot);record.putAll(map("state","loading_unknown","side",newRoot.location.getGlobalSide().name().toLowerCase(Locale.ROOT),"job_id",replacementJobId,"job_revision",signature(next.values().stream().map(r->r.signature).toArray()),"authority","native-simulator","physical_load_verified",false,"simulator_origin",true,"change_action","faulted-attempt-continuation","initial_fixture_registration",false,"registration","invalidated","load_revision","load-"+(revision+1),"updated_at",Instant.now().toString()));
            Map<String,Object> intent=map("schema_version",1,"receipt_id",step.stepId(),"step_id",step.stepId(),"continuation_id",authority.get("continuation_id"),"replacement_attempt_id",authority.get("replacement_attempt_id"),"recovery_operation_id",authority.get("recovery_operation_id"),"fault_set_sha256",authority.get("fault_set_sha256"),"continuation_capture_sha256",authority.get("continuation_capture_sha256"),"original_recovery_operation_id",authority.get("original_recovery_operation_id"),"original_fault_set_sha256",authority.get("original_fault_set_sha256"),"phase_row_sha256",authority.get("phase_row_sha256"),"mode",effective.equals("untouched")?"replace-untouched":"supersede-unknown","parent_type",parent==null?null:parent.get("type"),"parent_receipt_id",parent==null?null:parent.get("receipt_id"),"parent_sha256",parent==null?null:parent.get("payload_sha256"),"revision",revision+1,"old_load_id",originalOldLoadId,"old_load",old,"previous_load_id",previous.get("load_id"),"previous_load",previous,"old_native_history",history(oldJob,oldRoot),"new_load",record,"old_job_id",captured.get("job_id"),"new_job_id",replacementJobId,"original_quarantined_by",quarantinedLoads.get(originalOldLoadId),"previous_quarantined_by",quarantinedLoads.get(previous.get("load_id")),"old_disposition","quarantined-uncertain-attempt","execution_authority_restored",false,"physical_load_verified",false);
            requireContinuationStepNative(oldJob,fresh,root,intent);step.check();appendContinuationStep("board_continuation_intent",intent);quarantinedNativeJobs.add(oldJob);step.acceptForcedEvent("board_continuation_intent",intent);step.check();requireContinuationStepNative(oldJob,fresh,root,intent);
            Map<String,Object> outcome=NativeFaultedJobReplacement.mutable(intent);NativeFaultedJobReplacement.object(outcome.get("new_load")).put("state","loaded");appendContinuationStep("board_continuation_outcome",outcome);step.acceptForcedEvent("board_continuation_outcome",outcome);step.check();requireContinuationStepNative(oldJob,fresh,root,outcome);return continuationStepOutcomes.get(step.stepId());
        }
    }
    private void requireContinuationStepNative(Job oldJob,Job fresh,String rootId,Map<String,Object> intent)throws Exception {
        requireDetachedReplacement(oldJob,fresh);Root original=inspect(oldJob).get(rootId),next=inspect(fresh).get(rootId);Map<String,Object> load=record(intent.get("new_load"));if(original==null||next==null||!next.signature.equals(load.get("signature"))||!next.location.getGlobalSide().name().equalsIgnoreCase((String)load.get("side"))||!NativeFaultedJobReplacement.same(boards(next,(Map<?,?>)load.get("board_ids")),load.get("boards"))||!NativeFaultedJobReplacement.same(history(oldJob,original),intent.get("old_native_history"))||!history(fresh,next).isEmpty())throw fault("BOARD_LOAD_HISTORY_MISMATCH","Native continuation candidate root or original history changed");
    }
    private void appendContinuationStep(String type,Map<String,Object> p)throws Exception {
        Map<String,Object> frozen=NativeFaultedJobReplacement.frozen(p);Runnable commit=prepareContinuationStep(type,frozen);try{sink.append(type,frozen);commit.run();}catch(Exception|Error failure){continuationPublicationFault=true;refresh();throw failure;}
    }
    private Runnable prepareContinuationStep(String type,Map<String,Object> supplied)throws Exception {
        Map<String,Object> p=NativeFaultedJobReplacement.frozen(supplied);NativeFaultedJobReplacement.keys(p,"schema_version","receipt_id","step_id","continuation_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","continuation_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","phase_row_sha256","mode","parent_type","parent_receipt_id","parent_sha256","revision","old_load_id","old_load","previous_load_id","previous_load","old_native_history","new_load","old_job_id","new_job_id","original_quarantined_by","previous_quarantined_by","old_disposition","execution_authority_restored","physical_load_verified");
        if(NativeFaultedJobReplacement.number(p.get("schema_version"))!=1||!Set.of("replace-untouched","supersede-unknown").contains(p.get("mode"))||!"quarantined-uncertain-attempt".equals(p.get("old_disposition"))||!Boolean.FALSE.equals(p.get("execution_authority_restored"))||!Boolean.FALSE.equals(p.get("physical_load_verified")))throw fault("BOARD_LOAD_RECORD","Invalid board continuation scope");
        for(String key:List.of("receipt_id","step_id","continuation_id","replacement_attempt_id","recovery_operation_id","original_recovery_operation_id","old_load_id","previous_load_id","old_job_id","new_job_id"))NativeFaultedJobReplacement.uuid(p.get(key));for(String key:List.of("fault_set_sha256","continuation_capture_sha256","original_fault_set_sha256","phase_row_sha256"))NativeFaultedJobReplacement.hash(p.get(key));
        if(!Objects.equals(p.get("step_id"),p.get("receipt_id"))||!Objects.equals(p.get("replacement_attempt_id"),p.get("new_job_id"))||Objects.equals(p.get("old_job_id"),p.get("new_job_id"))||Objects.equals(p.get("recovery_operation_id"),p.get("original_recovery_operation_id")))throw fault("BOARD_LOAD_RECORD","Aliased continuation identities");
        String step=(String)p.get("step_id"),oldId=(String)p.get("old_load_id"),previousId=(String)p.get("previous_load_id");Map<String,Object> old=record(p.get("old_load")),previous=record(p.get("previous_load")),next=record(p.get("new_load"));String newId=(String)next.get("load_id"),root=(String)old.get("root_instance_id");long rev=number(p.get("revision"));
        Map<String,Object> nativeHistory=NativeFaultedJobReplacement.object(p.get("old_native_history"));Map<String,Object> historyRecord=copy(old);historyRecord.put("placed_history",nativeHistory);record(historyRecord);
        if(!oldId.equals(old.get("load_id"))||!previousId.equals(previous.get("load_id"))||!Objects.equals(old.get("job_id"),p.get("old_job_id"))||!Objects.equals(next.get("job_id"),p.get("new_job_id"))||!root.equals(previous.get("root_instance_id"))||!root.equals(next.get("root_instance_id"))||!Objects.equals(old.get("signature"),next.get("signature"))||!Objects.equals(previous.get("signature"),next.get("signature"))||!((Map<?,?>)next.get("placed_history")).isEmpty()||historyRegression((Map<?,?>)old.get("placed_history"),nativeHistory)!=null||!"faulted-attempt-continuation".equals(next.get("change_action"))||!"load-".concat(String.valueOf(rev)).equals(next.get("load_revision")))throw fault("BOARD_LOAD_RECORD","Continuation changes original scope or history");
        if(!NativeFaultedJobReplacement.same(old,loads.get(oldId))||!NativeFaultedJobReplacement.same(previous,loads.get(previousId)))throw fault("BOARD_LOAD_HISTORY_MISMATCH","Original or previous board load changed");
        if(type.equals("board_continuation_intent")){
            Map<String,Object> phase=currentContinuationRow((String)p.get("original_recovery_operation_id"),(String)p.get("original_fault_set_sha256"),(String)p.get("replacement_attempt_id"),oldId);String effective=effectivePhase(phase);Object parent=effectiveParent(phase,false);
            if(!NativeFaultedJobReplacement.continuationPhaseDigest(phase).equals(p.get("phase_row_sha256"))||!Objects.equals(active.get(root),previousId)||!Objects.equals(quarantinedLoads.get(oldId),p.get("original_quarantined_by"))||!Objects.equals(quarantinedLoads.get(previousId),p.get("previous_quarantined_by"))||quarantinedLoads.containsKey(previousId)||!pending.isEmpty()||continuationStepIntents.size()>=4096||continuationStepIntents.containsKey(step)||continuationAdoptions.containsKey(step)||replacementIntents.containsKey(step)||replacementReceipts.containsKey(step)||loads.containsKey(newId)||newId.equals(previousId)||rev!=revision+1||!"loading_unknown".equals(next.get("state")))throw fault("BOARD_CONTINUATION_SCOPE","Continuation lacks exact current unsuperseded root");
            if("replace-untouched".equals(p.get("mode"))){if(!"untouched".equals(effective)||!previousId.equals(oldId)||parent!=null||p.get("parent_type")!=null||p.get("parent_receipt_id")!=null||p.get("parent_sha256")!=null)throw fault("BOARD_CONTINUATION_SCOPE","Untouched replacement has prior effects");}
            else {if(!"pending".equals(effective)||parent==null||!"loading_unknown".equals(previous.get("state"))||!NativeFaultedJobReplacement.same(previous,effectiveNewLoad(phase)))throw fault("BOARD_CONTINUATION_SCOPE","Only the latest unknown root can be superseded");Map<String,Object> link=NativeFaultedJobReplacement.object(parent);if(!Objects.equals(link.get("type"),p.get("parent_type"))||!Objects.equals(link.get("receipt_id"),p.get("parent_receipt_id"))||!Objects.equals(link.get("payload_sha256"),p.get("parent_sha256"))||!NativeFaultedJobReplacement.same(NativeFaultedJobReplacement.object(link.get("payload")).get("old_native_history"),nativeHistory))throw fault("BOARD_CONTINUATION_SCOPE","Unknown parent receipt or original native history changed");}
            if(loads.size()>=MAX_LOADS)throw fault("BOARD_LOAD_CAPACITY","Retained board load capacity reached");int boards=0,histories=0;Set<Object> identities=new HashSet<>();for(Map<String,Object> load:loads.values()){Map<?,?> ids=(Map<?,?>)load.get("board_ids");boards+=ids.size();histories+=((Map<?,?>)load.get("placed_history")).size();identities.addAll(ids.values());}if(boards+((Map<?,?>)next.get("board_ids")).size()>MAX_RETAINED_BOARDS||histories>MAX_HISTORY||!Collections.disjoint(identities,((Map<?,?>)next.get("board_ids")).values()))throw fault("BOARD_LOAD_CAPACITY","Continuation board capacity or new identity conflict");
            return ()->{revision=rev;continuationStepIntents.put(step,p);quarantinedLoads.put(previousId,step);quarantinedJobs.putIfAbsent((String)p.get("old_job_id"),step);active.put(root,newId);loads.put(newId,next);confirmed.remove(root);refresh();};
        }
        if(!type.equals("board_continuation_outcome"))throw fault("BOARD_LOAD_RECORD","Unknown continuation event");Map<String,Object> comparison=NativeFaultedJobReplacement.mutable(p);NativeFaultedJobReplacement.object(comparison.get("new_load")).put("state","loading_unknown");Map<String,Object> intent=continuationStepIntents.get(step);
        if(intent==null||continuationStepOutcomes.containsKey(step)||!NativeFaultedJobReplacement.same(comparison,intent)||!"loaded".equals(next.get("state"))||!Objects.equals(active.get(root),newId)||!Objects.equals(quarantinedLoads.get(previousId),step)||!NativeFaultedJobReplacement.same(loads.get(newId),intent.get("new_load"))||rev!=revision)throw fault("BOARD_LOAD_RECORD","Continuation outcome lacks its exact current pending intent");
        return ()->{loads.put(newId,next);continuationStepOutcomes.put(step,p);refresh();};
    }
    private void appendReplacement(String type,Map<String,Object> p)throws Exception {Runnable commit=prepareReplacement(type,p);sink.append(type,NativeFaultedJobReplacement.frozen(p));commit.run();}
    private Runnable prepareReplacement(String type,Map<String,Object> supplied)throws Exception {
        Map<String,Object> p=NativeFaultedJobReplacement.frozen(supplied);NativeFaultedJobReplacement.keys(p,"schema_version","receipt_id","recovery_operation_id","fault_set_sha256","revision","old_load_id","old_load","old_native_history","new_load","old_job_id","new_job_id","old_disposition","physical_load_verified");
        if(NativeFaultedJobReplacement.number(p.get("schema_version"))!=1||!Boolean.FALSE.equals(p.get("physical_load_verified"))||!"quarantined-uncertain-attempt".equals(p.get("old_disposition")))throw fault("BOARD_LOAD_RECORD","Invalid replacement scope");
        String receipt=NativeFaultedJobReplacement.uuid(p.get("receipt_id")),oldId=NativeFaultedJobReplacement.uuid(p.get("old_load_id")),oldJob=NativeFaultedJobReplacement.uuid(p.get("old_job_id")),newJob=NativeFaultedJobReplacement.uuid(p.get("new_job_id"));NativeFaultedJobReplacement.uuid(p.get("recovery_operation_id"));NativeFaultedJobReplacement.hash(p.get("fault_set_sha256"));
        Map<String,Object> old=record(p.get("old_load")),fresh=record(p.get("new_load"));String freshId=(String)fresh.get("load_id"),root=(String)fresh.get("root_instance_id");long rev=number(p.get("revision"));
        if(!oldJob.equals(old.get("job_id"))||oldJob.equals(newJob)||!newJob.equals(fresh.get("job_id"))||!Objects.equals(old.get("signature"),fresh.get("signature"))||!Objects.equals(old.get("root_instance_id"),root)||!((Map<?,?>)fresh.get("placed_history")).isEmpty()||!Collections.disjoint(((Map<?,?>)old.get("board_ids")).values(),((Map<?,?>)fresh.get("board_ids")).values())||historyRegression((Map<?,?>)old.get("placed_history"),NativeFaultedJobReplacement.object(p.get("old_native_history")))!=null)throw fault("BOARD_LOAD_RECORD","Replacement changes scope or reuses old history/identity");
        if(type.equals("board_replacement_intent")){
            if(!pending.isEmpty()||!replacementIntents.isEmpty()||replacementReceipts.containsKey(receipt)||quarantinedLoads.containsKey(oldId)||loads.containsKey(freshId)||loads.size()>=MAX_LOADS||!Objects.equals(active.get(root),oldId)||!NativeFaultedJobReplacement.same(old,loads.get(oldId))||rev!=revision+1||!"loading_unknown".equals(fresh.get("state")))throw fault("BOARD_LOAD_RECORD","Replacement intent lacks exact old load");
            return ()->{revision=rev;replacementIntents.put(receipt,p);quarantinedLoads.put(oldId,receipt);quarantinedJobs.put(oldJob,receipt);active.put(root,freshId);loads.put(freshId,fresh);confirmed.clear();refresh();};
        }
        if(!type.equals("board_replacement_outcome"))throw fault("BOARD_LOAD_RECORD","Unknown replacement event");
        Map<String,Object> comparison=NativeFaultedJobReplacement.mutable(p);NativeFaultedJobReplacement.object(comparison.get("new_load")).put("state","loading_unknown");
        if(!NativeFaultedJobReplacement.same(comparison,replacementIntents.get(receipt))||!"loaded".equals(fresh.get("state"))||!NativeFaultedJobReplacement.same(old,loads.get(oldId))||!Objects.equals(active.get(root),freshId)||quarantinedLoads.containsKey(freshId))throw fault("BOARD_LOAD_RECORD","Replacement outcome lacks exact intent/history");
        return ()->{loads.put(freshId,fresh);replacementIntents.remove(receipt);replacementReceipts.put(receipt,p);refresh();};
    }

    /** Validate a detached candidate only; no registry/history/presence binding is changed. */
    static void validateCandidate(Job job)throws Exception { inspect(job); }
    static void validateCandidateForCompletedRead(Job job)throws Exception {inspect(job,true);}

    private static Map<String,Root> inspect(Job job)throws Exception {return inspect(job,false);}
    private static Map<String,Root> inspect(Job job,boolean completedRead)throws Exception {
        if(job==null)throw fault("NO_JOB","Prepare a native job first");
        PanelLocation virtual=job.getRootPanelLocation();Location virtualPose=virtual.getLocation().convertToUnits(LengthUnit.Millimeters);
        if(virtual.getParent()!=null||virtual.getGlobalSide()!=Side.Top||!virtual.isLocallyEnabled()||virtual.isCheckFiducials()||virtualPose.getX()!=0||virtualPose.getY()!=0||virtualPose.getZ()!=0||virtualPose.getRotation()!=0||!(completedRead?NativeReplacementJob.transformForCompletedRead(virtual):virtual.getLocalToParentTransform()).isIdentity()||!virtual.getPanel().getPlacements().isEmpty()||!virtual.getPanel().getPseudoPlacements().isEmpty())throw fault("BOARD_LOAD_ROOT_FRAME_UNSUPPORTED","Only the native neutral inline root frame is supported");
        Map<String,Root> roots=new LinkedHashMap<>();int[] bounds={0,0};Set<String> locationIds=new HashSet<>();Set<PlacementsHolderLocation<?>> objects=Collections.newSetFromMap(new IdentityHashMap<>());
        for(PlacementsHolderLocation<?> location:job.getRootPanelLocation().getChildren()){
            if(roots.size()>=100)throw fault("BOARD_LOAD_CAPACITY","Too many native roots");Root root=new Root(location);List<Object> structure=new ArrayList<>();walk(location,location,job.getRootPanelLocation(),root.boards,structure,bounds,locationIds,objects,0);root.signature=signature(structure);if(roots.put(root.id,root)!=null)throw fault("BOARD_LOAD_ID_CONFLICT","Native root IDs are ambiguous");
        }
        return roots;
    }
    private static void walk(PlacementsHolderLocation<?> root,PlacementsHolderLocation<?> loc,PanelLocation expectedParent,List<BoardLocation> boards,List<Object> structure,int[] bounds,Set<String> locationIds,Set<PlacementsHolderLocation<?>> objects,int depth)throws Exception {
        if(depth>8||++bounds[0]>MAX_BOARDS)throw fault("BOARD_LOAD_CAPACITY","Native hierarchy exceeds load mapping bounds");
        String expectedId=expectedParent.getUniqueId()==null?loc.getId():expectedParent.getUniqueId()+PlacementsHolderLocation.ID_DELIMITTER+loc.getId();
        if(loc.getParent()!=expectedParent||!Objects.equals(expectedId,loc.getUniqueId()))throw fault("BOARD_LOAD_ID_CONFLICT","Native holder parent and traversed root scope disagree");
        if(!objects.add(loc)||!locationIds.add(id(loc.getUniqueId()))||loc.getId()==null||loc.getId().contains(PlacementsHolderLocation.ID_DELIMITTER))throw fault("BOARD_LOAD_ID_CONFLICT","Native holder identity is aliased or contains the native history delimiter");
        List<Object> placements=new ArrayList<>();
        if(loc instanceof BoardLocation){BoardLocation b=(BoardLocation)loc;boards.add(b);}
        Set<String> placementIds=new HashSet<>();for(Placement p:loc.getPlacementsHolder().getPlacements()){
            if(++bounds[1]>MAX_PLACEMENTS||p.getId().contains(PlacementsHolderLocation.ID_DELIMITTER)||!placementIds.add(id(p.getId())))throw fault("BOARD_LOAD_ID_CONFLICT","Native placement IDs or count are invalid");
            placements.add(map("id",p.getId(),"part",p.getPart()==null?null:p.getPart().getId(),"side",p.getSide().name(),"type",p.getType().name(),"enabled",p.isEnabled(),"pose",pose(p.getLocation()),"rank",p.getRank(),"comments",p.getComments(),"error_handling",p.getErrorHandling().name()));
        }
        List<Object> pseudo=new ArrayList<>();if(loc instanceof PanelLocation)for(Placement p:((PanelLocation)loc).getPanel().getPseudoPlacements())pseudo.add(map("id",id(p.getId()),"pose",pose(p.getLocation()),"side",p.getSide().name(),"enabled",p.isEnabled(),"type",p.getType().name(),"part",p.getPart()==null?null:p.getPart().getId()));
        structure.add(map("id",id(loc.getUniqueId()),"kind",loc.getClass().getName(),"pose",pose(loc.getLocation()),"side",loc==root?"root-side-separate":loc.getSide().name(),"enabled",loc.isLocallyEnabled(),"dimensions",pose(loc.getPlacementsHolder().getDimensions()),"check_fiducials",loc.isCheckFiducials(),"placements",placements,"pseudo_placement_ids",pseudo));
        if(loc instanceof PanelLocation)for(PlacementsHolderLocation<?> child:((PanelLocation)loc).getChildren())walk(root,child,(PanelLocation)loc,boards,structure,bounds,locationIds,objects,depth+1);
    }
    private static Map<String,Object> pose(Location loc){Location p=loc.convertToUnits(LengthUnit.Millimeters);for(double value:new double[]{p.getX(),p.getY(),p.getZ(),p.getRotation()})if(!Double.isFinite(value))throw new IllegalArgumentException("Nonfinite native pose");return map("x",p.getX(),"y",p.getY(),"z",p.getZ(),"rotation",p.getRotation());}
    private static final class Root{final String id;final PlacementsHolderLocation<?> location;final List<BoardLocation> boards=new ArrayList<>();String signature;Root(PlacementsHolderLocation<?> location){this.location=location;this.id=location.getUniqueId();}}
    private static String signature(Object value)throws Exception{byte[] bytes=MessageDigest.getInstance("SHA-256").digest(GSON.toJson(value).getBytes(StandardCharsets.UTF_8));StringBuilder out=new StringBuilder();for(byte b:bytes)out.append(String.format("%02x",b));return out.toString();}
    private static String id(Object value){if(!(value instanceof String)||((String)value).isEmpty()||((String)value).length()>512||((String)value).chars().anyMatch(c->c<32||c==127))throw new IllegalArgumentException("Invalid native load identity");return (String)value;}
    private static long number(Object value){return NativeJournalJson.integer(value,1,9007199254740991L);}
    private static Bridge.Fault fault(String code,String message){return new Bridge.Fault(code,message);}
    private static Map<String,Object> map(Object... pairs){Map<String,Object> result=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)result.put((String)pairs[i],pairs[i+1]);return result;}
    private static Map<String,Object> copy(Map<?,?> source){return NativeJournalJson.copy(source);}
    private static Map<String,Object> immutable(Map<String,Object> source){return Collections.unmodifiableMap(copy(source));}
}
