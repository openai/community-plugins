/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.spi.*;

/** PRIVATE simulator prototype. Forced Bridge journal is authority for observations only.
 * Does not infer a physical refill, pickup, stock count, or restart machine ownership.
 * prepareEvent is pure; its returned commit runs only AFTER the existing journal force.
 * Native mutations and before/after hook observations require the current native executor.
 */
public final class NativeMaterialLoads implements NativeActionLedger.MaterialObserver {
    public interface Sink { void append(String type,Map<String,Object> payload)throws Exception; }
    public static final int MAX_LOADS=512,MAX_FEED_ACTIONS=100000,MAX_CONTINUATION_ADOPTIONS=4096;
    private static final Gson GSON=new Gson();
    private final Configuration config;private final Machine machine;private final Sink sink;
    private final Map<String,Map<String,Object>> loads=new LinkedHashMap<>(),changes=new LinkedHashMap<>(),feeds=new LinkedHashMap<>();
    private final Map<String,String> active=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> replacementIntents=new LinkedHashMap<>(),replacementReceipts=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> continuationIntents=new LinkedHashMap<>(),continuationOutcomes=new LinkedHashMap<>();
    private final Map<String,String> continuationSupersededBy=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> continuationAdoptions=new LinkedHashMap<>();
    private final Map<String,String> retiredLoads=new LinkedHashMap<>();
    private final Map<String,Binding> bindings=new LinkedHashMap<>(),pendingWitnesses=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> restartObservations=new LinkedHashMap<>();
    private final Map<String,RestartWitness> restartWitnesses=new LinkedHashMap<>();
    private boolean restartPublicationFaulted;
    private static final class RestartWitness {
        final NativeFaultedJobReplacement.RestartPermit permit;final Map<String,Object> receipt;final Binding binding;
        RestartWitness(NativeFaultedJobReplacement.RestartPermit p,Map<String,Object> r,Binding b){permit=p;receipt=r;binding=b;}
    }
    private final List<Map<String,Object>> inventory=new ArrayList<>();
    private String inventoryObservedAt;private long revision;private boolean recovered,continuationAdoptionFaulted,continuationMutationFaulted;private volatile Map<String,Object> snapshot;
    private static final class Binding { final ReferenceTrayFeeder feeder;final Part part;final org.openpnp.model.Package pkg;final String fingerprint;
        Binding(ReferenceTrayFeeder f,String fp){feeder=f;part=f.getPart();pkg=part.getPackage();fingerprint=fp;}}
    public NativeMaterialLoads(Configuration config,Sink sink)throws Exception{
        this.config=Objects.requireNonNull(config);machine=config.getMachine();this.sink=Objects.requireNonNull(sink);
        captureInventory();refresh();
    }
    /** Called only from an existing executor-owned Bridge refresh; reads no hardware. */
    public void observeConfiguration()throws Exception{owner();captureInventory();refresh();}
    private void captureInventory()throws Exception{
        if(machine.getFeeders().size()>1000)throw fault("MATERIAL_CAPACITY","Feeder inventory exceeds 1000");
        List<Map<String,Object>> next=new ArrayList<>();for(Feeder f:machine.getFeeders())if(f.getClass()==ReferenceTrayFeeder.class){try{next.add(describe((ReferenceTrayFeeder)f));}catch(Bridge.Fault unsupported){next.add(map("feeder_id",f.getId(),"supported",false,"code",unsupported.code));}}
        inventory.clear();inventory.addAll(next);inventoryObservedAt=Instant.now().toString();
    }
    public String revision(){return "material-"+revision;}
    public Map<String,Object> snapshot(){return snapshot;}
    public boolean enrolled(String feederId){return active.containsKey(feederId);}
    public boolean hasEnrolled(){return !active.isEmpty();}
    public void requireRevision(String value)throws Bridge.Fault{if(!revision().equals(value))throw fault("MATERIAL_REVISION_CONFLICT","Material setup revision changed");}
    private void owner()throws Exception{if(Configuration.get()!=config||config.getMachine()!=machine||!machine.isTask(Thread.currentThread()))throw fault("MATERIAL_OWNER_REQUIRED","Exact current native configuration and executor required");}
    private void empty()throws Exception{for(Head h:machine.getHeads())for(Nozzle n:h.getNozzles())if(n.getPart()!=null)throw fault("NOZZLE_OCCUPIED","Tray changeover requires empty native nozzles");}
    private boolean hasUnretiredFeeds(){for(Map<String,Object> feed:feeds.values())if(!retiredLoads.containsKey(objectUnchecked(feed.get("material_load")).get("load_id")))return true;return false;}
    private static Map<String,Object> objectUnchecked(Object value){return (Map<String,Object>)value;}
    private void unresolved()throws Bridge.Fault{if(!changes.isEmpty()||hasCurrentReplacementIntent()||hasUnretiredFeeds())throw fault("MATERIAL_OUTCOME_UNKNOWN","Preserve unresolved change/feed; no automatic retry or replacement");}
    private ReferenceTrayFeeder selected(String id)throws Exception{Feeder raw=machine.getFeeder(id);if(raw==null||raw.getClass()!=ReferenceTrayFeeder.class)throw fault("MATERIAL_PROFILE_UNSUPPORTED","Only existing exact ReferenceTrayFeeder is supported");ReferenceTrayFeeder f=(ReferenceTrayFeeder)raw;
        Part part=f.getPart();if(part==null||config.getPart(part.getId())!=part||part.getPackage()==null||config.getPackage(part.getPackage().getId())!=part.getPackage())throw fault("MATERIAL_MODEL_CHANGED","Canonical registered native part and package objects required before publication");
        int identities=0;for(Feeder candidate:machine.getFeeders())if(Objects.equals(candidate.getId(),id))identities++;if(identities!=1)throw fault("MATERIAL_MODEL_CHANGED","Ambiguous native feeder identifier");
        describe(f);return f;}
    private void sole(ReferenceTrayFeeder f)throws Exception{for(Feeder other:machine.getFeeders())if(other!=f&&other.isEnabled()&&other.getPart()==f.getPart())throw fault("MATERIAL_AMBIGUOUS_FEEDER","This slice requires one enabled feeder for the enrolled part");}
    private Binding binding(String feeder)throws Exception{return binding(feeder,false);}
    private Binding binding(String feeder,boolean completedRead)throws Exception{
        Binding b=bindings.get(feeder);if(b==null)throw fault("MATERIAL_REATTACH_UNSUPPORTED","Recovered history supplies no native authority; use a new isolated state");
        if(machine.getFeeder(feeder)!=b.feeder||b.feeder.getPart()!=b.part||config.getPart(b.part.getId())!=b.part||b.part.getPackage()!=b.pkg||config.getPackage(b.pkg.getId())!=b.pkg||!fingerprint(b.feeder,completedRead).equals(b.fingerprint))throw fault("MATERIAL_MODEL_CHANGED","Native feeder/part/package identity or geometry changed");
        sole(b.feeder);return b;
    }
    public void requireReady()throws Exception{owner();requireCurrentBindings();}
    /** Bounded read-only completion check under the exact live publication witness. */
    void requireReadyForPublication(NativeFaultedJobReplacement.Publication publication)throws Exception{if(publication==null)throw fault("MATERIAL_OWNER_REQUIRED","Exact publication witness required");publication.authorizeCompletedRead(this);requireCurrentBindings(true);publication.authorizeCompletedRead(this);}
    private void requireCurrentBindings()throws Exception{requireCurrentBindings(false);}
    private void requireCurrentBindings(boolean completedRead)throws Exception{unresolved();for(String feeder:active.keySet()){Binding b=binding(feeder,completedRead);Map<String,Object> load=loads.get(active.get(feeder));if(b.feeder.getFeedCount()!=integer(load.get("current_index"),0,10000))throw fault("MATERIAL_INDEX_CHANGED","Native index differs from forced material history");}}
    public Map<String,Object> register(String feederId,String partId,String fingerprint,String action,String previousId,String expectedRevision)throws Exception{
        owner();requireRevision(expectedRevision);if(machine.isEnabled())throw fault("MACHINE_ENABLED","Disable before virtual tray changeover");empty();unresolved();
        if(!Set.of("bind-existing","replace-full-tray").contains(action))throw fault("INVALID_ARGUMENT","Unknown material load action");
        ReferenceTrayFeeder f=selected(feederId);sole(f);Map<String,Object> described=describe(f);
        if(!Objects.equals(f.getPart().getId(),partId)||!Objects.equals(described.get("geometry_sha256"),fingerprint))throw fault("MATERIAL_MODEL_CHANGED","Select exact observed part and geometry digest");
        String previous=active.get(feederId);if(!Objects.equals(previous,previousId))throw fault("MATERIAL_LOAD_CONFLICT","Expected prior material load differs");
        if("bind-existing".equals(action)&&previous!=null)throw fault("MATERIAL_ALREADY_BOUND","Existing load may only be replaced explicitly");
        if("replace-full-tray".equals(action)&&previous==null)throw fault("MATERIAL_LOAD_REQUIRED","Bind the existing tray before replacing it");
        if(previous!=null){binding(feederId);requireReady();}
        if(loads.size()>=MAX_LOADS)throw fault("MATERIAL_CAPACITY","Retained load capacity reached; nothing is evicted");
        int before=f.getFeedCount(),after="bind-existing".equals(action)?before:0;
        String loadId=UUID.randomUUID().toString(),changeId=UUID.randomUUID().toString();
        Map<String,Object> record=map("schema_version",1,"load_id",loadId,"feeder_id",feederId,"part_id",partId,"geometry_sha256",fingerprint,"capacity",described.get("capacity"),"initial_index",after,"current_index",after,"observed_advances",0,"state","loading_unknown","created_at",Instant.now().toString());
        Map<String,Object> intent=map("schema_version",1,"change_id",changeId,"revision",revision+1,"previous_load_id",previousId,"action",action,"before_index",before,"record",record);
        sink.append("material_load_intent",intent); // must force AND commit reducer before returning
        if("replace-full-tray".equals(action))f.setFeedCount(0);
        if(f.getFeedCount()!=after||!fingerprint(f).equals(fingerprint))throw fault("MATERIAL_MODEL_CHANGED","Native model changed during material setter");
        config.save(); // no promise that a failed save did not change native memory or disk
        Map<String,Object> outcome=copy(intent);Map<String,Object> ready=copy(record);ready.put("state","loaded");outcome.put("record",ready);
        sink.append("material_load_outcome",outcome);bindings.put(feederId,new Binding(f,fingerprint));refresh();return map("material_setup_revision",revision(),"load",view(loadId),"physical_inventory_verified",false,"authority","native-simulator","job_validation","requires-revalidation","requires_validation",true);
    }
    public Map<String,Object> beforeFeed(Feeder feeder)throws Exception{
        owner();if(!enrolled(feeder.getId()))return new LinkedHashMap<>();requireReady();Binding b=binding(feeder.getId());if(feeder!=b.feeder)throw fault("MATERIAL_MODEL_CHANGED","Native hook refers to another feeder object");
        Map<String,Object> load=loads.get(active.get(feeder.getId()));int index=b.feeder.getFeedCount();if(index>=integer(load.get("capacity"),1,10000))throw fault("MATERIAL_EMPTY","No configured virtual tray slot remains");
        return map("load_id",load.get("load_id"),"material_setup_revision",revision(),"feeder_id",feeder.getId(),"part_id",b.part.getId(),"geometry_sha256",b.fingerprint,"before_index",index,"capacity",load.get("capacity"),"physical_inventory_verified",false);
    }
    public Map<String,Object> afterFeed(Feeder feeder,Map<String,Object> material)throws Exception{
        owner();if(material.isEmpty())return new LinkedHashMap<>();Binding b=binding((String)material.get("feeder_id"));
        if(feeder!=b.feeder||!Objects.equals(active.get(feeder.getId()),material.get("load_id")))throw fault("MATERIAL_MODEL_CHANGED","Feed completion has another native load");
        int index=b.feeder.getFeedCount();return map("after_index",index,"expected_delta_observed",index==integer(material.get("before_index"),0,10000)+1,"observation","native-feedCount-at-after-hook","physical_inventory_verified",false);
    }
    /** Shared journal invokes this BEFORE write/force. No native reads or mutation here. */
    public Runnable prepareEvent(String type,Map<String,Object> payload)throws Exception{
        if(type.equals("material_restart_observation"))return prepareRestartObservation(payload);
        if(Set.of("material_continuation_intent","material_continuation_outcome").contains(type))return prepareContinuationMutation(type,payload);
        if(type.startsWith("material_continuation_"))return prepareContinuationAdoption(type,payload);
        if(type.startsWith("material_replacement_"))return prepareReplacement(type,payload);
        if(type.startsWith("material_load_"))return prepareChange(type,payload);
        if(!Set.of("native_action_intent","native_action_outcome").contains(type)||!"feed".equals(payload.get("kind")))return ()->{};
        Map<String,Object> context=object(payload.get("context"));Object raw=context.get("material_load");
        String feeder=text(context.get("feeder_id"));if(raw==null){if(enrolled(feeder))throw fault("MATERIAL_BINDING_MISSING","Enrolled feed has no material context");return ()->{};}
        Map<String,Object> material=object(raw);exact(material,"load_id","material_setup_revision","feeder_id","part_id","geometry_sha256","before_index","capacity","physical_inventory_verified");
        String loadId=uuid(material.get("load_id")),action=text(payload.get("action_id"));Map<String,Object> load=loads.get(loadId);
        if(load==null||!Objects.equals(active.get(feeder),loadId)||!"loaded".equals(load.get("state"))||!Objects.equals(material.get("feeder_id"),feeder)||!Objects.equals(material.get("part_id"),load.get("part_id"))||!Objects.equals(material.get("geometry_sha256"),load.get("geometry_sha256"))||!Boolean.FALSE.equals(material.get("physical_inventory_verified"))||!revision().equals(material.get("material_setup_revision"))||integer(material.get("capacity"),1,10000)!=integer(load.get("capacity"),1,10000))throw fault("MATERIAL_RECORD_INVALID","Feed context has no exact active load");
        if(!Objects.equals(context.get("part_id"),material.get("part_id"))||!"Feeder.BeforeFeed".equals(payload.get("hook"))||!"unknown".equals(payload.get("physical_outcome")))throw fault("MATERIAL_RECORD_INVALID","Native feed hook, part context, or observation scope differs");
        int before=integer(material.get("before_index"),0,9999);Map<String,Object> stable=copy(material);
        if("native_action_intent".equals(type)){
            if(!"intent".equals(payload.get("state"))||!changes.isEmpty()||hasCurrentReplacementIntent()||hasUnretiredFeeds()||before!=integer(load.get("current_index"),0,10000)||before>=integer(load.get("capacity"),1,10000))throw fault("MATERIAL_RECORD_INVALID","Unresolved or discontinuous feed intent");
            if(integer(load.get("observed_advances"),0,MAX_FEED_ACTIONS)>=MAX_FEED_ACTIONS)throw fault("MATERIAL_CAPACITY","Material action observation limit");
            Map<String,Object> pending=map("action_id",action,"operation_id",text(payload.get("operation_id")),"material_load",stable,"state","outcome_unknown");
            return ()->{feeds.put(action,pending);refresh();};
        }
        Map<String,Object> pending=feeds.get(action);if(pending==null||!stable.equals(pending.get("material_load"))||!Objects.equals(pending.get("operation_id"),payload.get("operation_id")))throw fault("MATERIAL_RECORD_INVALID","Feed outcome has no exact intent");
        if(!"Feeder.AfterFeed".equals(payload.get("after_hook"))||!Set.of("native_hook_returned","outcome_unknown").contains(payload.get("state")))throw fault("MATERIAL_RECORD_INVALID","Unexpected feed outcome state/hook");
        Map<String,Object> after=object(payload.get("material_after"));exact(after,"after_index","expected_delta_observed","observation","physical_inventory_verified");int index=integer(after.get("after_index"),0,10000);
        boolean advanced=index==before+1;
        if(!Boolean.valueOf(advanced).equals(after.get("expected_delta_observed"))||!Boolean.FALSE.equals(after.get("physical_inventory_verified"))||!"native-feedCount-at-after-hook".equals(after.get("observation"))||advanced!= "native_hook_returned".equals(payload.get("state")))throw fault("MATERIAL_RECORD_INVALID","Unexpected native feed index delta");
        if(!advanced){Map<String,Object> unknown=copy(pending);unknown.put("material_after",copy(after));return ()->{feeds.put(action,unknown);refresh();};}
        return ()->{load.put("current_index",index);load.put("observed_advances",((Number)load.get("observed_advances")).intValue()+1);feeds.remove(action);refresh();};
    }
    private Runnable prepareChange(String type,Map<String,Object> payload)throws Exception{
        if(!Set.of("material_load_intent","material_load_outcome").contains(type))throw fault("MATERIAL_RECORD_INVALID","Unknown material record");
        // Gson's existing journal encoding omits null-valued map entries. Only this
        // explicitly nullable field may be absent; normalize it before strict validation.
        payload=copy(payload);payload.putIfAbsent("previous_load_id",null);
        exact(payload,"schema_version","change_id","revision","previous_load_id","action","before_index","record");if(integer(payload.get("schema_version"),1,1)!=1)throw fault("MATERIAL_RECORD_INVALID","Schema version");
        Map<String,Object> row=object(payload.get("record"));exact(row,"schema_version","load_id","feeder_id","part_id","geometry_sha256","capacity","initial_index","current_index","observed_advances","state","created_at");integer(row.get("schema_version"),1,1);
        String id=uuid(row.get("load_id")),feeder=text(row.get("feeder_id")),part=text(row.get("part_id"));String fp=text(row.get("geometry_sha256"));if(!fp.matches("[0-9a-f]{64}"))throw fault("MATERIAL_RECORD_INVALID","Invalid geometry digest");
        Instant.parse(text(row.get("created_at")));int capacity=integer(row.get("capacity"),1,10000),initial=integer(row.get("initial_index"),0,capacity);if(integer(row.get("current_index"),0,capacity)!=initial||integer(row.get("observed_advances"),0,0)!=0)throw fault("MATERIAL_RECORD_INVALID","New load count mismatch");
        String change=uuid(payload.get("change_id")),action=text(payload.get("action"));Object previous=payload.get("previous_load_id");if(previous!=null)uuid(previous);long rev=integer(payload.get("revision"),1,MAX_LOADS);int before=integer(payload.get("before_index"),0,capacity);Map<String,Object> retained=copy(payload);
        if("material_load_intent".equals(type)){
            if(loads.size()>=MAX_LOADS)throw fault("MATERIAL_CAPACITY","Retained load limit");
            if(!changes.isEmpty()||hasCurrentReplacementIntent()||hasUnretiredFeeds()||rev!=revision+1||loads.containsKey(id)||!Objects.equals(active.get(feeder),previous)||!"loading_unknown".equals(row.get("state")))throw fault("MATERIAL_RECORD_INVALID","Invalid change intent");
            if("bind-existing".equals(action)){if(previous!=null||initial!=before)throw fault("MATERIAL_RECORD_INVALID","Invalid initial binding");}
            else if("replace-full-tray".equals(action)){Map<String,Object> old=loads.get(previous);if(old==null||initial!=0||!"loaded".equals(old.get("state"))||!part.equals(old.get("part_id"))||!fp.equals(old.get("geometry_sha256"))||before!=integer(old.get("current_index"),0,capacity)||capacity!=integer(old.get("capacity"),1,10000))throw fault("MATERIAL_RECORD_INVALID","Invalid same-part replacement");}
            else throw fault("MATERIAL_RECORD_INVALID","Unknown change action");
            return ()->{revision=rev;changes.put(change,retained);loads.put(id,copy(row));refresh();};
        }
        Map<String,Object> intent=changes.get(change);if(intent==null)throw fault("MATERIAL_RECORD_INVALID","Change outcome missing intent");Map<String,Object> comparison=copy(retained);object(comparison.get("record")).put("state","loading_unknown");if(!comparison.equals(intent)||!"loaded".equals(row.get("state")))throw fault("MATERIAL_RECORD_INVALID","Change outcome differs from intent");
        return ()->{loads.put(id,copy(row));active.put(feeder,id);changes.remove(change);refresh();};
    }
    /** Complete immutable load/change/feed facts; no native authority is exported. */
    Map<String,Object> captureReplacementState(){Map<String,Object> state=map("revision",revision(),"loads",loads,"active",active,"pending_changes",changes,"pending_feeds",feeds,"replacement_intents",replacementIntents,"retired_loads",retiredLoads);if(!continuationIntents.isEmpty())state.putAll(map("continuation_intents",continuationIntents,"continuation_outcomes",continuationOutcomes,"continuation_superseded_by",continuationSupersededBy,"current_unresolved_replacement_intents",currentReplacementIntents(),"current_unresolved_continuation_intents",currentContinuationIntents()));return freeze(state);}
    private Map<String,Object> currentReplacementIntents(){Map<String,Object> result=new LinkedHashMap<>();for(var entry:replacementIntents.entrySet())if(!continuationSupersededBy.containsKey(entry.getKey()))result.put(entry.getKey(),entry.getValue());return freeze(result);}
    private Map<String,Object> currentContinuationIntents(){Map<String,Object> result=new LinkedHashMap<>();for(var entry:continuationIntents.entrySet())if(!continuationOutcomes.containsKey(entry.getKey())&&!continuationSupersededBy.containsKey(entry.getKey()))result.put(entry.getKey(),entry.getValue());return freeze(result);}
    private boolean hasCurrentReplacementIntent(){return !currentReplacementIntents().isEmpty()||!currentContinuationIntents().isEmpty();}
    Map<String,Object> continuationMutationReceipt(String receiptId){return continuationOutcomes.get(receiptId);}

    Map<String,Object> replacementReceipt(String receiptId){return replacementReceipts.get(receiptId);}
    /** Authoritative historical receipt only; never supplies a retained native binding. */
    Map<String,Object> continuationAdoptionReceipt(String receiptId){return continuationAdoptions.get(receiptId);}
    /** Reducer-only phase capture; the owning Bridge serializes this with publication/replay.
     * The transaction owner must bind originalLoadIds and untouched facts to its original capture.
     * No native getters, setters, binding restoration or execution authority are supplied here. */
    Map<String,Object> replacementProgress(String recoveryOperationId,String faultDigest,List<String> originalLoadIds)throws Exception {
        uuid(recoveryOperationId);if(faultDigest==null||!faultDigest.matches("[a-f0-9]{64}"))throw fault("MATERIAL_REPLACEMENT_SCOPE","Exact fault digest required");
        if(originalLoadIds==null||originalLoadIds.isEmpty()||originalLoadIds.size()>MAX_LOADS)throw fault("MATERIAL_REPLACEMENT_SCOPE","Bounded nonempty original load union required");
        Set<String> ids=new TreeSet<>();for(String id:originalLoadIds)if(!ids.add(uuid(id)))throw fault("MATERIAL_REPLACEMENT_SCOPE","Original load union contains a duplicate");
        Map<String,Map<String,Object>> parents=new TreeMap<>();Set<String> receiptIds=new HashSet<>(),newIds=new HashSet<>();
        for(Map<String,Map<String,Object>> records:List.of(replacementIntents,replacementReceipts))for(Map.Entry<String,Map<String,Object>> entry:records.entrySet()){
            Map<String,Object> record=entry.getValue();String receipt=uuid(record.get("receipt_id")),oldId=uuid(record.get("old_load_id"));
            boolean sameOperation=Objects.equals(recoveryOperationId,record.get("recovery_operation_id")),sameFault=Objects.equals(faultDigest,record.get("fault_set_sha256"));
            if(!entry.getKey().equals(receipt))throw fault("MATERIAL_REPLACEMENT_SCOPE","Replacement receipt key differs from its record");
            if(sameOperation&&(!sameFault||!ids.contains(oldId))||ids.contains(oldId)&&(!sameOperation||!sameFault))throw fault("MATERIAL_REPLACEMENT_SCOPE","Foreign or omitted replacement receipt");
            if(!sameOperation)continue;
            String newId=uuid(object(record.get("new_load")).get("load_id"));
            if(!receiptIds.add(receipt)||!newIds.add(newId)||ids.contains(newId)||parents.put(oldId,record)!=null)throw fault("MATERIAL_REPLACEMENT_SCOPE","Repeated or overlapping replacement receipt/load identity");
        }
        for(Map<String,Object> record:continuationIntents.values()){String oldId=uuid(record.get("old_load_id"));boolean sameOperation=Objects.equals(recoveryOperationId,record.get("original_recovery_operation_id")),sameFault=Objects.equals(faultDigest,record.get("original_fault_set_sha256"));if(sameOperation&&(!sameFault||!ids.contains(oldId))||ids.contains(oldId)&&(!sameOperation||!sameFault))throw fault("MATERIAL_REPLACEMENT_SCOPE","Foreign or omitted continuation receipt");}
        List<Object> rows=new ArrayList<>();Set<String> feeders=new HashSet<>();int untouched=0,pending=0,completed=0;
        for(String oldId:ids){Map<String,Object> row=replacementProgressRow(recoveryOperationId,faultDigest,oldId,parents.get(oldId));if(!feeders.add((String)row.get("feeder_id")))throw fault("MATERIAL_REPLACEMENT_SCOPE","Original union aliases a feeder");String phase=(String)row.get("phase");if(phase.equals("untouched"))untouched++;else if(phase.equals("pending"))pending++;else completed++;rows.add(row);}
        return NativeFaultedJobReplacement.frozen(map("schema_version",1,"recovery_operation_id",recoveryOperationId,"fault_set_sha256",faultDigest,
            "material_setup_revision",revision(),"original_load_ids",new ArrayList<>(ids),"rows",rows,"pending_ordinary_changes",changes,
            "counts",map("untouched",untouched,"pending",pending,"completed",completed),"original_capture_required_for_untouched",true,
            "native_observations",null,"execution_authority_restored",false,"physical_inventory_verified",false));
    }
    private Map<String,Object> originalParent(String operation,String digest,String oldId)throws Exception {
        Map<String,Object> parent=null;for(var records:List.of(replacementIntents,replacementReceipts))for(var entry:records.entrySet()){Map<String,Object> value=entry.getValue();if(!Objects.equals(oldId,value.get("old_load_id")))continue;if(parent!=null||!entry.getKey().equals(value.get("receipt_id"))||!Objects.equals(operation,value.get("recovery_operation_id"))||!Objects.equals(digest,value.get("fault_set_sha256")))throw fault("MATERIAL_REPLACEMENT_SCOPE","Original replacement parent is repeated or foreign");parent=value;}return parent;
    }
    private Map<String,Object> replacementProgressRow(String operation,String digest,String oldId,Map<String,Object> parent)throws Exception {
        Map<String,Object> old=loads.get(oldId);if(old==null||!oldId.equals(old.get("load_id"))||!"loaded".equals(old.get("state")))throw fault("MATERIAL_REPLACEMENT_SCOPE","Original loaded material record is missing or changed");
        String feeder=text(old.get("feeder_id")),currentId=active.get(feeder);Map<String,Object> current=loads.get(currentId),intent=null,outcome=null,replacementCurrent=null;String phase;boolean stillActive=false,matches=false;
        List<Map<String,Object>> chain=continuationChain(operation,digest,oldId,parent);
        if(parent==null){phase="untouched";if(chain.isEmpty()&&(!Objects.equals(currentId,oldId)||retiredLoads.containsKey(oldId)))throw fault("MATERIAL_REPLACEMENT_SCOPE","Untouched load is not the exact unretired active load");}
        else {String receipt=uuid(parent.get("receipt_id")),newId=uuid(object(parent.get("new_load")).get("load_id"));boolean done=replacementReceipts.containsKey(receipt);phase=done?"completed":"pending";
            if(!NativeFaultedJobReplacement.same(old,parent.get("old_load"))||!NativeFaultedJobReplacement.same(feedFacts(oldId),parent.get("pending_feeds")))throw fault("MATERIAL_REPLACEMENT_SCOPE","Original load or captured feed facts changed");replacementCurrent=loads.get(newId);stillActive=Objects.equals(currentId,newId);matches=NativeFaultedJobReplacement.same(current,parent.get("new_load"));
            if(done){if(!Objects.equals(retiredLoads.get(oldId),receipt)||!"loaded".equals(object(parent.get("new_load")).get("state"))||replacementCurrent==null)throw fault("MATERIAL_REPLACEMENT_SCOPE","Completed replacement lacks exact retirement or retained load");requireRetainedLoadIdentity(replacementCurrent,object(parent.get("new_load")));outcome=parent;intent=copy(parent);object(intent.get("new_load")).put("state","loading_unknown");}
            else {if(chain.isEmpty()&&(!stillActive||!matches||retiredLoads.containsKey(oldId))||!NativeFaultedJobReplacement.same(replacementCurrent,parent.get("new_load"))||!"loading_unknown".equals(object(parent.get("new_load")).get("state")))throw fault("MATERIAL_REPLACEMENT_SCOPE","Pending replacement has a conflicting disposition");intent=parent;}
        }
        Map<String,Object> row=map("old_load_id",oldId,"feeder_id",feeder,"phase",phase,"old_load",old,"old_feed_facts",feedFacts(oldId),"current_load_id",currentId,"current_load",current,"new_load",parent==null?null:parent.get("new_load"),"replacement_load_current",replacementCurrent,"current_matches_replacement_receipt",matches,"replacement_still_active",stillActive,"parent_intent",replacementParent("material_replacement_intent",intent),"parent_outcome",replacementParent("material_replacement_outcome",outcome),"intent_reconstructed_from_outcome",outcome!=null,"retired_by",retiredLoads.get(oldId),"binding_status",map("retained_binding_present",bindings.containsKey(feeder),"active_load_id",currentId,"observation","retained-only; native identity and geometry not inspected","execution_authority_restored",false));
        if(!chain.isEmpty()){Map<String,Object> last=chain.get(chain.size()-1),effectiveIntent=object(object(last.get("intent")).get("payload")),effectiveOutcome=last.get("outcome")==null?null:object(object(last.get("outcome")).get("payload")),effective=effectiveOutcome==null?effectiveIntent:effectiveOutcome,fresh=object(effective.get("new_load"));String id=(String)fresh.get("load_id");row.putAll(map("continuation_chain",chain,"effective_phase",effectiveOutcome==null?"pending":"completed","effective_parent_intent",last.get("intent"),"effective_parent_outcome",last.get("outcome"),"effective_new_load",fresh,"effective_load_current",loads.get(id),"effective_load_active",Objects.equals(currentId,id),"effective_load_matches_receipt",NativeFaultedJobReplacement.same(current,fresh)));}
        return freeze(row);
    }
    private static void requireRetainedLoadIdentity(Map<String,Object> current,Map<String,Object> receipt)throws Exception {if(current==null)throw fault("MATERIAL_REPLACEMENT_SCOPE","Retained replacement load is missing");for(String key:List.of("load_id","feeder_id","part_id","geometry_sha256","capacity","initial_index","created_at"))if(!NativeFaultedJobReplacement.same(current.get(key),receipt.get(key)))throw fault("MATERIAL_REPLACEMENT_SCOPE","Retained replacement identity changed");}
    private List<Map<String,Object>> continuationChain(String operation,String digest,String oldId,Map<String,Object> original)throws Exception {
        List<Map<String,Object>> rows=new ArrayList<>();Map<String,Object> previous=original;String previousType=original==null?null:replacementReceipts.containsKey(original.get("receipt_id"))?"material_replacement_outcome":"material_replacement_intent";Set<String> newIds=new HashSet<>();
        for(var entry:continuationIntents.entrySet()){Map<String,Object> intent=entry.getValue();if(!Objects.equals(oldId,intent.get("old_load_id")))continue;String id=uuid(intent.get("receipt_id"));if(!entry.getKey().equals(id)||!Objects.equals(operation,intent.get("original_recovery_operation_id"))||!Objects.equals(digest,intent.get("original_fault_set_sha256"))||!NativeFaultedJobReplacement.same(loads.get(oldId),intent.get("old_load"))||!NativeFaultedJobReplacement.same(feedFacts(oldId),intent.get("pending_feeds")))throw fault("MATERIAL_REPLACEMENT_SCOPE","Continuation changed original scope/load/feed facts");
            requireMutationParent(intent,previousType,previous);if(previousType!=null&&!Objects.equals(continuationSupersededBy.get(previous.get("receipt_id")),id))throw fault("MATERIAL_REPLACEMENT_SCOPE","Pending parent has no exact supersession disposition");
            Map<String,Object> outcome=continuationOutcomes.get(id),latest=outcome==null?intent:outcome,fresh=object(latest.get("new_load"));String newId=uuid(fresh.get("load_id"));if(!newIds.add(newId)||newId.equals(oldId))throw fault("MATERIAL_REPLACEMENT_SCOPE","Continuation repeats a load identity");requireRetainedLoadIdentity(loads.get(newId),fresh);if(outcome==null&&!NativeFaultedJobReplacement.same(loads.get(newId),fresh))throw fault("MATERIAL_REPLACEMENT_SCOPE","Unknown continuation load changed");
            rows.add(freeze(map("receipt_id",id,"step_id",intent.get("step_id"),"continuation_id",intent.get("continuation_id"),"new_load_id",newId,"generation",rows.size()+1,"phase",outcome==null?"pending":"completed","intent",replacementParent("material_continuation_intent",intent),"outcome",replacementParent("material_continuation_outcome",outcome),"superseded_by",continuationSupersededBy.get(id))));previous=latest;previousType=outcome==null?"material_continuation_intent":"material_continuation_outcome";
        }
        if(!rows.isEmpty()){Map<String,Object> last=rows.get(rows.size()-1);if(continuationSupersededBy.containsKey(last.get("receipt_id")))throw fault("MATERIAL_REPLACEMENT_SCOPE","Continuation chain omits a superseding child");Map<String,Object> latest=object(object(last.get(last.get("outcome")==null?"intent":"outcome")).get("payload"));if(last.get("outcome")==null&&!Objects.equals(active.get(object(latest.get("new_load")).get("feeder_id")),object(latest.get("new_load")).get("load_id")))throw fault("MATERIAL_REPLACEMENT_SCOPE","Current unknown continuation is not the exact active candidate");}
        return rows;
    }
    private void requireMutationParent(Map<String,Object> intent,String type,Map<String,Object> parent)throws Exception {
        if(type==null){if(!"replace-untouched".equals(intent.get("mode"))||intent.get("parent_type")!=null||intent.get("parent_receipt_id")!=null||intent.get("parent_sha256")!=null||!Objects.equals(intent.get("old_load_id"),intent.get("previous_load_id"))||!NativeFaultedJobReplacement.same(intent.get("old_load"),intent.get("previous_load")))throw fault("MATERIAL_RECORD_INVALID","Untouched continuation has a foreign parent");}
        else {if(!Set.of("material_replacement_intent","material_continuation_intent").contains(type)||!"supersede-unknown".equals(intent.get("mode"))||!Objects.equals(type,intent.get("parent_type"))||!Objects.equals(parent.get("receipt_id"),intent.get("parent_receipt_id"))||!NativeFaultedJobReplacement.digest(parent).equals(intent.get("parent_sha256"))||!NativeFaultedJobReplacement.same(parent.get("new_load"),intent.get("previous_load"))||!Objects.equals(object(parent.get("new_load")).get("load_id"),intent.get("previous_load_id")))throw fault("MATERIAL_RECORD_INVALID","Continuation cannot supersede this exact unknown parent");}
    }
    private static Map<String,Object> replacementParent(String type,Map<String,Object> record)throws Exception {
        return record==null?null:map("type",type,"receipt_id",record.get("receipt_id"),"payload_sha256",NativeFaultedJobReplacement.digest(record),"payload",record);
    }
    /** Fresh restart observation has no ordinary feed authority. Only a separately authorized
     * continuation may consume its sealed, process-local identity witness. Replay keeps facts only. */
    Map<String,Object> observeRestart(NativeFaultedJobReplacement.RestartPermit permit,String oldLoadId)throws Exception {
        owner();if(restartPublicationFaulted)throw fault("MATERIAL_RESTART_UNCERTAIN","Restart observation publication is uncertain; verify a new replay instance");
        if(machine.isEnabled())throw fault("MACHINE_ENABLED","Disable before restart tray observation");empty();
        Map<String,Object> authority=permit.authorizeMaterial(this,oldLoadId);exact(authority,"reattachment_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","restart_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","old_material","phase_row");
        Map<String,Object> row=restartRow(authority,oldLoadId),old=object(row.get("old_load")),original=object(authority.get("old_material"));
        if(!NativeFaultedJobReplacement.same(object(original.get("loads")).get(oldLoadId),old)||!Objects.equals(object(original.get("active")).get(old.get("feeder_id")),oldLoadId)||!NativeFaultedJobReplacement.continuationPhaseDigest(row).equals(NativeFaultedJobReplacement.continuationPhaseDigest(object(authority.get("phase_row")))))throw fault("MATERIAL_RESTART_SCOPE","Restart observation differs from exact captured original load and phase");
        Map<String,Object> observation=restartNativeObservation(row);ReferenceTrayFeeder feeder=selected((String)row.get("feeder_id"));Binding freshWitness=new Binding(feeder,(String)old.get("geometry_sha256"));
        Map<String,Object> descriptor=restartParent(row),current=object(row.get("current_load"));Map<String,Object> receipt=map("schema_version",1,"receipt_id",UUID.randomUUID().toString(),"old_load_id",oldLoadId,"phase",row.getOrDefault("effective_phase",row.get("phase")),"phase_row_sha256",NativeFaultedJobReplacement.continuationPhaseDigest(row),"old_load_sha256",NativeFaultedJobReplacement.digest(old),"old_feed_facts_sha256",NativeFaultedJobReplacement.digest(object(row.get("old_feed_facts"))),"current_load_id",row.get("current_load_id"),"current_load_sha256",NativeFaultedJobReplacement.digest(current),"parent_type",descriptor==null?null:descriptor.get("type"),"parent_receipt_id",descriptor==null?null:descriptor.get("receipt_id"),"parent_sha256",descriptor==null?null:descriptor.get("payload_sha256"),"material_setup_revision",revision(),"native_observation",observation,"binding_scope","continuation-only","original_outcomes_preserved",true,"execution_authority_restored",false,"physical_inventory_verified",false);
        for(String key:List.of("reattachment_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","restart_capture_sha256","original_recovery_operation_id","original_fault_set_sha256"))receipt.put(key,authority.get(key));
        prepareRestartObservation(receipt);permit.check();
        try{sink.append("material_restart_observation",receipt);if(!NativeFaultedJobReplacement.same(restartObservations.get(receipt.get("receipt_id")),receipt))throw fault("MATERIAL_RECORD_INVALID","Restart append returned without exact committed observation");}
        catch(Exception|Error failure){restartPublicationFaulted=true;restartWitnesses.remove(oldLoadId);refresh();throw failure;}
        permit.acceptForcedEvent("material_restart_observation",receipt);permit.check();validateRestartFacts(receipt);
        if(!NativeFaultedJobReplacement.same(observation,restartNativeObservation(row))||selected(feeder.getId())!=freshWitness.feeder||feeder.getPart()!=freshWitness.part||feeder.getPart().getPackage()!=freshWitness.pkg)throw fault("MATERIAL_MODEL_CHANGED","Restart tray identity or observation changed during force");
        restartWitnesses.put(oldLoadId,new RestartWitness(permit,restartObservations.get(receipt.get("receipt_id")),freshWitness));refresh();return restartObservations.get(receipt.get("receipt_id"));
    }
    Map<String,Object> restartObservationReceipt(String receiptId){return restartObservations.get(receiptId);}
    private Map<String,Object> restartRow(Map<String,Object> authority,String oldId)throws Exception {
        String operation=uuid(authority.get("original_recovery_operation_id")),digest=(String)authority.get("original_fault_set_sha256");digestText(digest);
        return replacementProgressRow(operation,digest,uuid(oldId),originalParent(operation,digest,oldId));
    }
    private static Map<String,Object> restartParent(Map<String,Object> row)throws Exception {String phase=(String)row.getOrDefault("effective_phase",row.get("phase"));return phase.equals("untouched")?null:object(row.getOrDefault(phase.equals("completed")?"effective_parent_outcome":"effective_parent_intent",row.get(phase.equals("completed")?"parent_outcome":"parent_intent")));}
    private Map<String,Object> restartNativeObservation(Map<String,Object> row)throws Exception {
        owner();if(machine.isEnabled())throw fault("MACHINE_ENABLED","Restart tray observation requires disabled machine");empty();Map<String,Object> old=object(row.get("old_load")),current=object(row.get("current_load"));ReferenceTrayFeeder f=selected((String)old.get("feeder_id"));sole(f);Map<String,Object> actual=describe(f);
        for(String key:List.of("part_id","geometry_sha256","capacity"))if(!NativeFaultedJobReplacement.same(old.get(key),actual.get(key)))throw fault("MATERIAL_MODEL_CHANGED","Restart native tray differs from original "+key);
        if("completed".equals(row.getOrDefault("effective_phase",row.get("phase")))){Map<String,Object> fresh=object(object(restartParent(row).get("payload")).get("new_load"));if(!NativeFaultedJobReplacement.same(current,fresh)||!"loaded".equals(fresh.get("state"))||!feedFacts((String)fresh.get("load_id")).isEmpty()||integer(fresh.get("initial_index"),0,0)!=0||integer(fresh.get("current_index"),0,0)!=0||integer(fresh.get("observed_advances"),0,0)!=0||f.getFeedCount()!=0)throw fault("MATERIAL_INDEX_CHANGED","Completed replacement requires an exact untouched loaded receipt and zero-index native tray");}
        return map("feeder_id",f.getId(),"part_id",f.getPart().getId(),"package_id",f.getPart().getPackage().getId(),"geometry_sha256",actual.get("geometry_sha256"),"capacity",actual.get("capacity"),"native_feed_count",f.getFeedCount(),"index_matches_durable_load",NativeFaultedJobReplacement.same(current.get("current_index"),f.getFeedCount()),"machine_disabled",true,"nozzles_empty",true,"observation","fresh-native-simulator-on-owning-executor","physical_inventory_verified",false);
    }
    private Runnable prepareRestartObservation(Map<String,Object> source)throws Exception {
        Map<String,Object> p=copy(source);for(String key:List.of("parent_type","parent_receipt_id","parent_sha256"))p.putIfAbsent(key,null);
        exact(p,"schema_version","receipt_id","reattachment_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","restart_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","old_load_id","phase","phase_row_sha256","old_load_sha256","old_feed_facts_sha256","current_load_id","current_load_sha256","parent_type","parent_receipt_id","parent_sha256","material_setup_revision","native_observation","binding_scope","original_outcomes_preserved","execution_authority_restored","physical_inventory_verified");integer(p.get("schema_version"),1,1);
        for(String key:List.of("receipt_id","reattachment_id","replacement_attempt_id","recovery_operation_id","original_recovery_operation_id","old_load_id","current_load_id"))uuid(p.get(key));for(String key:List.of("fault_set_sha256","restart_capture_sha256","original_fault_set_sha256","phase_row_sha256","old_load_sha256","old_feed_facts_sha256","current_load_sha256"))digestText(p.get(key));
        if(!"continuation-only".equals(p.get("binding_scope"))||!Boolean.TRUE.equals(p.get("original_outcomes_preserved"))||!Boolean.FALSE.equals(p.get("execution_authority_restored"))||!Boolean.FALSE.equals(p.get("physical_inventory_verified")))throw fault("MATERIAL_RECORD_INVALID","Restart observation cannot restore execution authority");
        String receipt=(String)p.get("receipt_id");if(restartObservations.size()>=MAX_CONTINUATION_ADOPTIONS||restartObservations.containsKey(receipt)||replacementIntents.containsKey(receipt)||replacementReceipts.containsKey(receipt)||continuationIntents.containsKey(receipt)||continuationAdoptions.containsKey(receipt)||changes.containsKey(receipt))throw fault("MATERIAL_RESTART_REPEATED","Restart observation capacity or receipt identity conflict");
        for(Map<String,Object> previous:restartObservations.values())if(Objects.equals(previous.get("reattachment_id"),p.get("reattachment_id"))&&Objects.equals(previous.get("old_load_id"),p.get("old_load_id")))throw fault("MATERIAL_RESTART_REPEATED","This restart decision already observed the tray");
        validateRestartFacts(p);Map<String,Object> retained=freeze(p);return ()->{restartObservations.put(receipt,retained);refresh();};
    }
    private void validateRestartFacts(Map<String,Object> p)throws Exception {validateRestartFacts(p,false);}
    private void validateRestartFacts(Map<String,Object> p,boolean laterContinuation)throws Exception {
        Map<String,Object> row=restartRow(p,(String)p.get("old_load_id")),old=object(row.get("old_load")),current=object(row.get("current_load")),parent=restartParent(row);String phase=(String)row.getOrDefault("effective_phase",row.get("phase"));
        if(!changes.isEmpty()||!Objects.equals(phase,p.get("phase"))||!Objects.equals(row.get("current_load_id"),p.get("current_load_id"))||!laterContinuation&&!revision().equals(p.get("material_setup_revision"))||!NativeFaultedJobReplacement.continuationPhaseDigest(row).equals(p.get("phase_row_sha256"))||!NativeFaultedJobReplacement.digest(old).equals(p.get("old_load_sha256"))||!NativeFaultedJobReplacement.digest(object(row.get("old_feed_facts"))).equals(p.get("old_feed_facts_sha256"))||!NativeFaultedJobReplacement.digest(current).equals(p.get("current_load_sha256")))throw fault("MATERIAL_RESTART_SCOPE","Restart observation changed exact load/feed/phase facts");
        if(!Objects.equals(parent==null?null:parent.get("type"),p.get("parent_type"))||!Objects.equals(parent==null?null:parent.get("receipt_id"),p.get("parent_receipt_id"))||!Objects.equals(parent==null?null:parent.get("payload_sha256"),p.get("parent_sha256")))throw fault("MATERIAL_RESTART_SCOPE","Restart parent receipt differs");
        if(phase.equals("completed"))completedAdoptionRow((String)p.get("original_recovery_operation_id"),(String)p.get("original_fault_set_sha256"),(String)p.get("old_load_id"));
        Map<String,Object> n=object(p.get("native_observation"));exact(n,"feeder_id","part_id","package_id","geometry_sha256","capacity","native_feed_count","index_matches_durable_load","machine_disabled","nozzles_empty","observation","physical_inventory_verified");text(n.get("package_id"));int index=integer(n.get("native_feed_count"),0,integer(old.get("capacity"),1,10000));
        for(String key:List.of("feeder_id","part_id","geometry_sha256","capacity"))if(!NativeFaultedJobReplacement.same(old.get(key),n.get(key)))throw fault("MATERIAL_RESTART_SCOPE","Restart native fact differs: "+key);
        if(phase.equals("completed")&&index!=0||!Boolean.valueOf(NativeFaultedJobReplacement.same(index,current.get("current_index"))).equals(n.get("index_matches_durable_load"))||!Boolean.TRUE.equals(n.get("machine_disabled"))||!Boolean.TRUE.equals(n.get("nozzles_empty"))||!Boolean.FALSE.equals(n.get("physical_inventory_verified"))||!"fresh-native-simulator-on-owning-executor".equals(n.get("observation")))throw fault("MATERIAL_RECORD_INVALID","Invalid restart native observation");
    }
    private Binding restartBinding(NativeFaultedJobReplacement.ContinuationPermit currentPermit,Map<String,Object> authority,String oldId,Map<String,Object> row)throws Exception {
        currentPermit.check();RestartWitness witness=restartWitnesses.get(oldId);if(witness==null||restartPublicationFaulted)throw fault("MATERIAL_REATTACH_UNSUPPORTED","Exact sealed current-process restart observation is required");
        for(String key:List.of("replacement_attempt_id","original_recovery_operation_id","original_fault_set_sha256"))if(!Objects.equals(authority.get(key),witness.receipt.get(key)))throw fault("MATERIAL_RESTART_SCOPE","Continuation differs from sealed restart scope");
        witness.permit.requireSealedMaterial(this,oldId,(String)witness.receipt.get("receipt_id"));validateRestartFacts(witness.receipt,true);
        if(!NativeFaultedJobReplacement.continuationPhaseDigest(row).equals(witness.receipt.get("phase_row_sha256"))||!NativeFaultedJobReplacement.same(restartNativeObservation(row),witness.receipt.get("native_observation")))throw fault("MATERIAL_MODEL_CHANGED","Restart tray observation changed before continuation");
        Binding b=witness.binding;if(selected(b.feeder.getId())!=b.feeder||b.feeder.getPart()!=b.part||b.part.getPackage()!=b.pkg||config.getPackage(b.pkg.getId())!=b.pkg)throw fault("MATERIAL_MODEL_CHANGED","Restart native tray identity changed");currentPermit.check();return b;
    }
    /** Explicit native executor observation, separate from the retained phase facts. It never binds
     * a recovered load or changes its phase because an observed native counter happens to be zero. */
    Map<String,Object> observeReplacementProgress(String recoveryOperationId,String faultDigest,List<String> originalLoadIds)throws Exception {
        owner();Map<String,Object> result=NativeFaultedJobReplacement.mutable(replacementProgress(recoveryOperationId,faultDigest,originalLoadIds));List<Object> observations=new ArrayList<>();
        for(Object raw:(List<?>)result.get("rows")){
            Map<String,Object> row=object(raw),old=object(row.get("old_load")),current=object(row.get("current_load"));String feeder=(String)row.get("feeder_id");
            Map<String,Object> observation=map("old_load_id",row.get("old_load_id"),"feeder_id",feeder,"native_observation_available",false,"current_geometry_sha256",null,"current_index",null,"current_part_id",null,
                "geometry_matches_original",false,"index_matches_current_load",false,"retained_binding_matches_current_objects",false,"native_error_code",null);
            try{
                ReferenceTrayFeeder nativeFeeder=selected(feeder);Map<String,Object> described=describe(nativeFeeder);sole(nativeFeeder);Binding retained=bindings.get(feeder);
                observation.putAll(map("native_observation_available",true,"current_geometry_sha256",described.get("geometry_sha256"),"current_index",described.get("native_feed_count"),"current_part_id",described.get("part_id"),
                    "geometry_matches_original",Objects.equals(old.get("geometry_sha256"),described.get("geometry_sha256")),"index_matches_current_load",NativeFaultedJobReplacement.same(current.get("current_index"),described.get("native_feed_count")),
                    "retained_binding_matches_current_objects",retained!=null&&retained.feeder==nativeFeeder&&retained.part==nativeFeeder.getPart()&&retained.pkg==nativeFeeder.getPart().getPackage()&&Objects.equals(retained.fingerprint,described.get("geometry_sha256"))));
            }catch(Bridge.Fault unavailable){observation.put("native_error_code",unavailable.code);}
            observations.add(observation);
        }
        result.put("native_observations",map("observation","current-native-simulator-model-on-owning-executor","loads",observations,"execution_authority_restored",false,"physical_inventory_verified",false));
        return NativeFaultedJobReplacement.frozen(result);
    }
    Map<String,Object> replaceRetiredLoad(NativeFaultedJobReplacement.Permit permit,String oldLoadId)throws Exception {
        owner();if(machine.isEnabled())throw fault("MACHINE_ENABLED","Disable before explicit simulator tray replacement");empty();
        Map<String,Object> authority=permit.authorizeMaterial(this,oldLoadId),old=object(authority.get("old_load"));
        if(!changes.isEmpty()||hasCurrentReplacementIntent()||!NativeFaultedJobReplacement.same(old,loads.get(oldLoadId))||!Objects.equals(active.get(old.get("feeder_id")),oldLoadId))throw fault("MATERIAL_OUTCOME_UNKNOWN","Exact captured active tray without a pending change required");
        if(loads.size()>=MAX_LOADS)throw fault("MATERIAL_CAPACITY","Retained load capacity reached");
        ReferenceTrayFeeder f=selected((String)old.get("feeder_id"));sole(f);Map<String,Object> described=describe(f);
        if(!Objects.equals(old.get("part_id"),described.get("part_id"))||!Objects.equals(old.get("geometry_sha256"),described.get("geometry_sha256")))throw fault("MATERIAL_MODEL_CHANGED","Replacement tray geometry/part differs from captured load");
        Map<String,Object> pending=feedFacts(oldLoadId);if(!NativeFaultedJobReplacement.same(pending,authority.get("pending_feeds")))throw fault("MATERIAL_OUTCOME_UNKNOWN","Captured unknown feed union changed");
        String receiptId=UUID.randomUUID().toString(),newId=UUID.randomUUID().toString();Map<String,Object> fresh=map("schema_version",1,"load_id",newId,"feeder_id",old.get("feeder_id"),"part_id",old.get("part_id"),"geometry_sha256",old.get("geometry_sha256"),"capacity",old.get("capacity"),"initial_index",0,"current_index",0,"observed_advances",0,"state","loading_unknown","created_at",Instant.now().toString());
        Map<String,Object> intent=map("schema_version",1,"receipt_id",receiptId,"recovery_operation_id",authority.get("recovery_operation_id"),"fault_set_sha256",authority.get("fault_set_sha256"),"revision",revision+1,"old_load_id",oldLoadId,"old_load",old,"pending_feeds",pending,"native_index_before",f.getFeedCount(),"new_load",fresh,"old_disposition","consumption_unknown_or_consumed_preserved","physical_inventory_verified",false);
        sink.append("material_replacement_intent",intent);pendingWitnesses.put(receiptId,new Binding(f,(String)old.get("geometry_sha256")));permit.check();
        // Counter initialization belongs only to the already admitted new load; old record never changes.
        f.setFeedCount(0);permit.check();if(f.getFeedCount()!=0||!Objects.equals(fingerprint(f),old.get("geometry_sha256")))throw fault("MATERIAL_MODEL_CHANGED","Native tray setter did not establish exact new load");config.save();permit.check();if(selected(f.getId())!=f||f.getFeedCount()!=0||!Objects.equals(fingerprint(f),old.get("geometry_sha256")))throw fault("MATERIAL_MODEL_CHANGED","Native replacement changed during save");sole(f);
        Map<String,Object> outcome=copy(intent);object(outcome.get("new_load")).put("state","loaded");sink.append("material_replacement_outcome",outcome);
        bindings.put(f.getId(),new Binding(f,(String)old.get("geometry_sha256")));refresh();return replacementReceipts.get(receiptId);
    }
    /** Execute a fresh explicitly scoped replacement for an untouched or unknown target. */
    Map<String,Object> continueReplacement(NativeFaultedJobReplacement.ContinuationPermit permit,String oldLoadId)throws Exception {
        owner();if(continuationMutationFaulted||continuationAdoptionFaulted)throw fault("MATERIAL_CONTINUATION_UNCERTAIN","Material continuation publication is uncertain; verify a new journal replay instance");
        if(machine.isEnabled())throw fault("MACHINE_ENABLED","Disable before explicit simulator material continuation");empty();if(!changes.isEmpty())throw fault("MATERIAL_OUTCOME_UNKNOWN","An ordinary material change is unresolved");
        try(var step=permit.beginMaterialStep(this,oldLoadId)){
            Map<String,Object> authority=step.authority(),row=replacementProgressRow(text(authority.get("original_recovery_operation_id")),text(authority.get("original_fault_set_sha256")),oldLoadId,originalParent(text(authority.get("original_recovery_operation_id")),text(authority.get("original_fault_set_sha256")),oldLoadId));
            if(!NativeFaultedJobReplacement.continuationPhaseDigest(row).equals(authority.get("phase_row_sha256")))throw fault("MATERIAL_CONTINUATION_SCOPE","Material target changed since the exact continuation step capture");
            String phase=text(row.getOrDefault("effective_phase",row.get("phase")));if(!Set.of("untouched","pending").contains(phase))throw fault("MATERIAL_CONTINUATION_SCOPE","Completed material requires adoption, not another replacement");
            Map<String,Object> parentDescriptor=phase.equals("untouched")?null:object(row.getOrDefault("effective_parent_intent",row.get("parent_intent"))),parent=parentDescriptor==null?null:object(parentDescriptor.get("payload")),old=object(row.get("old_load")),previous=object(row.get("current_load"));String feeder=text(old.get("feeder_id"));
            ReferenceTrayFeeder nativeFeeder=selected(feeder);sole(nativeFeeder);Map<String,Object> observed=describe(nativeFeeder);Binding witness=phase.equals("untouched")?(bindings.containsKey(feeder)?binding(feeder):restartBinding(permit,authority,oldLoadId,row)):pendingWitnesses.get(parent.get("receipt_id"));if(witness==null)witness=restartBinding(permit,authority,oldLoadId,row);
            if(witness==null||witness.feeder!=nativeFeeder||witness.part!=nativeFeeder.getPart()||witness.pkg!=nativeFeeder.getPart().getPackage()||!Objects.equals(witness.fingerprint,observed.get("geometry_sha256"))||!Objects.equals(old.get("part_id"),observed.get("part_id"))||!Objects.equals(old.get("geometry_sha256"),observed.get("geometry_sha256"))||!NativeFaultedJobReplacement.same(old.get("capacity"),observed.get("capacity")))throw fault("MATERIAL_MODEL_CHANGED","Explicit continuation requires its exact retained native feeder/part/package and geometry");
            if(loads.size()>=MAX_LOADS)throw fault("MATERIAL_CAPACITY","Retained load capacity reached");String receipt=step.stepId(),newId=UUID.randomUUID().toString();
            Map<String,Object> fresh=map("schema_version",1,"load_id",newId,"feeder_id",feeder,"part_id",old.get("part_id"),"geometry_sha256",old.get("geometry_sha256"),"capacity",old.get("capacity"),"initial_index",0,"current_index",0,"observed_advances",0,"state","loading_unknown","created_at",Instant.now().toString());
            Map<String,Object> intent=map("schema_version",1,"receipt_id",receipt,"step_id",receipt,"old_load_id",oldLoadId,"old_load",old,"previous_load_id",previous.get("load_id"),"previous_load",previous,"parent_type",parentDescriptor==null?null:parentDescriptor.get("type"),"parent_receipt_id",parent==null?null:parent.get("receipt_id"),"parent_sha256",parent==null?null:NativeFaultedJobReplacement.digest(parent),"mode",phase.equals("untouched")?"replace-untouched":"supersede-unknown","new_load",fresh,"revision",revision+1,"pending_feeds",feedFacts(oldLoadId),"native_index_before",observed.get("native_feed_count"),"old_disposition","consumption_unknown_or_consumed_preserved","original_outcomes_preserved",true,"execution_authority_restored",false,"physical_inventory_verified",false);
            for(String key:List.of("continuation_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","continuation_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","phase_row_sha256"))intent.put(key,authority.get(key));
            prepareContinuationMutation("material_continuation_intent",intent);step.check();appendContinuationMutation("material_continuation_intent",intent);pendingWitnesses.put(receipt,new Binding(nativeFeeder,(String)old.get("geometry_sha256")));step.acceptForcedEvent("material_continuation_intent",intent);
            step.check();nativeFeeder.setFeedCount(0);step.check();requireContinuedNativeModel(nativeFeeder,witness,old);config.save();step.check();requireContinuedNativeModel(nativeFeeder,witness,old);
            Map<String,Object> outcome=copy(intent);object(outcome.get("new_load")).put("state","loaded");prepareContinuationMutation("material_continuation_outcome",outcome);step.check();appendContinuationMutation("material_continuation_outcome",outcome);bindings.put(feeder,new Binding(nativeFeeder,(String)old.get("geometry_sha256")));refresh();step.acceptForcedEvent("material_continuation_outcome",outcome);return continuationOutcomes.get(receipt);
        }
    }
    private void requireContinuedNativeModel(ReferenceTrayFeeder feeder,Binding witness,Map<String,Object> old)throws Exception {if(selected(feeder.getId())!=feeder||feeder!=witness.feeder||feeder.getPart()!=witness.part||feeder.getPart().getPackage()!=witness.pkg||feeder.getFeedCount()!=0||!Objects.equals(fingerprint(feeder),old.get("geometry_sha256")))throw fault("MATERIAL_MODEL_CHANGED","New continuation load changed during native reset/save");sole(feeder);}
    private void appendContinuationMutation(String type,Map<String,Object> record)throws Exception {
        try{sink.append(type,record);Map<String,Object> committed=(type.equals("material_continuation_intent")?continuationIntents:continuationOutcomes).get(record.get("receipt_id"));if(!NativeFaultedJobReplacement.same(committed,record))throw fault("MATERIAL_RECORD_INVALID","Material continuation append did not commit exact record");}
        catch(Exception|Error failure){continuationMutationFaulted=true;refresh();throw failure;}
    }
    private Runnable prepareContinuationMutation(String type,Map<String,Object> source)throws Exception {
        Map<String,Object> p=copy(source);for(String key:List.of("parent_type","parent_receipt_id","parent_sha256"))p.putIfAbsent(key,null);
        exact(p,"schema_version","receipt_id","step_id","continuation_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","continuation_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","phase_row_sha256","old_load_id","old_load","previous_load_id","previous_load","parent_type","parent_receipt_id","parent_sha256","mode","new_load","revision","pending_feeds","native_index_before","old_disposition","original_outcomes_preserved","execution_authority_restored","physical_inventory_verified");
        integer(p.get("schema_version"),1,1);String receipt=uuid(p.get("receipt_id")),oldId=uuid(p.get("old_load_id")),previousId=uuid(p.get("previous_load_id"));if(!receipt.equals(uuid(p.get("step_id"))))throw fault("MATERIAL_RECORD_INVALID","Material step and receipt differ");
        for(String key:List.of("continuation_id","replacement_attempt_id","recovery_operation_id","original_recovery_operation_id"))uuid(p.get(key));for(String key:List.of("fault_set_sha256","continuation_capture_sha256","original_fault_set_sha256","phase_row_sha256"))digestText(p.get(key));
        if(Objects.equals(p.get("recovery_operation_id"),p.get("original_recovery_operation_id"))||!Boolean.TRUE.equals(p.get("original_outcomes_preserved"))||!Boolean.FALSE.equals(p.get("execution_authority_restored"))||!Boolean.FALSE.equals(p.get("physical_inventory_verified"))||!"consumption_unknown_or_consumed_preserved".equals(p.get("old_disposition")))throw fault("MATERIAL_RECORD_INVALID","Material continuation scope is invalid");
        Map<String,Object> old=object(p.get("old_load")),previous=object(p.get("previous_load")),fresh=object(p.get("new_load"));exact(fresh,"schema_version","load_id","feeder_id","part_id","geometry_sha256","capacity","initial_index","current_index","observed_advances","state","created_at");integer(fresh.get("schema_version"),1,1);String newId=uuid(fresh.get("load_id")),feeder=text(fresh.get("feeder_id"));Instant.parse(text(fresh.get("created_at")));int capacity=integer(fresh.get("capacity"),1,10000),rev=integer(p.get("revision"),1,MAX_LOADS);
        integer(fresh.get("initial_index"),0,0);integer(fresh.get("current_index"),0,0);integer(fresh.get("observed_advances"),0,0);integer(p.get("native_index_before"),0,capacity);
        if(!oldId.equals(old.get("load_id"))||!previousId.equals(previous.get("load_id")))throw fault("MATERIAL_RECORD_INVALID","Continuation old/previous load IDs differ");for(String key:List.of("feeder_id","part_id","geometry_sha256","capacity"))if(!NativeFaultedJobReplacement.same(old.get(key),fresh.get(key))||!NativeFaultedJobReplacement.same(previous.get(key),fresh.get(key)))throw fault("MATERIAL_RECORD_INVALID","Continuation changes target part/geometry");
        if(type.equals("material_continuation_intent")){
            if(continuationIntents.size()>=MAX_LOADS||loads.size()>=MAX_LOADS)throw fault("MATERIAL_CAPACITY","Material continuation retained load limit");
            if(continuationIntents.containsKey(receipt)||replacementIntents.containsKey(receipt)||replacementReceipts.containsKey(receipt)||continuationAdoptions.containsKey(receipt)||loads.containsKey(newId)||newId.equals(previousId)||newId.equals(oldId)||!"loading_unknown".equals(fresh.get("state"))||rev!=revision+1||!changes.isEmpty()||!Objects.equals(active.get(feeder),previousId)||!NativeFaultedJobReplacement.same(loads.get(oldId),old)||!NativeFaultedJobReplacement.same(loads.get(previousId),previous)||!NativeFaultedJobReplacement.same(feedFacts(oldId),p.get("pending_feeds")))throw fault("MATERIAL_RECORD_INVALID","Material continuation intent does not bind exact current load/history");
            for(Map<String,Object> prior:continuationIntents.values())if(Objects.equals(p.get("continuation_id"),prior.get("continuation_id"))&&Objects.equals(oldId,prior.get("old_load_id")))throw fault("MATERIAL_CONTINUATION_REPEATED","This local continuation already attempted the target");
            for(Map<String,Object> prior:continuationAdoptions.values())if(Objects.equals(p.get("continuation_id"),prior.get("continuation_id"))&&Objects.equals(oldId,prior.get("old_load_id")))throw fault("MATERIAL_CONTINUATION_REPEATED","This local continuation already adopted the target");
            Map<String,Object> row=replacementProgressRow((String)p.get("original_recovery_operation_id"),(String)p.get("original_fault_set_sha256"),oldId,originalParent((String)p.get("original_recovery_operation_id"),(String)p.get("original_fault_set_sha256"),oldId));String phase=(String)row.getOrDefault("effective_phase",row.get("phase"));Map<String,Object> descriptor=phase.equals("untouched")?null:phase.equals("pending")?object(row.getOrDefault("effective_parent_intent",row.get("parent_intent"))):null;
            if(!Set.of("untouched","pending").contains(phase))throw fault("MATERIAL_CONTINUATION_SCOPE","Only untouched or unknown material can be explicitly replaced");requireMutationParent(p,descriptor==null?null:(String)descriptor.get("type"),descriptor==null?null:object(descriptor.get("payload")));
            if(p.get("parent_receipt_id")!=null&&continuationSupersededBy.containsKey(p.get("parent_receipt_id")))throw fault("MATERIAL_CONTINUATION_REPEATED","Unknown parent already has a superseding candidate");
            Map<String,Object> retained=freeze(p);return ()->{revision=rev;continuationIntents.put(receipt,retained);if(retained.get("parent_receipt_id")!=null)continuationSupersededBy.put((String)retained.get("parent_receipt_id"),receipt);loads.put(newId,copy(fresh));active.put(feeder,newId);bindings.remove(feeder);refresh();};
        }
        if(!type.equals("material_continuation_outcome"))throw fault("MATERIAL_RECORD_INVALID","Unknown material continuation mutation");Map<String,Object> intent=continuationIntents.get(receipt),comparison=copy(p);object(comparison.get("new_load")).put("state","loading_unknown");
        if(intent==null||continuationOutcomes.containsKey(receipt)||continuationSupersededBy.containsKey(receipt)||!NativeFaultedJobReplacement.same(intent,comparison)||!"loaded".equals(fresh.get("state"))||!Objects.equals(active.get(feeder),newId)||!NativeFaultedJobReplacement.same(loads.get(newId),intent.get("new_load"))||!NativeFaultedJobReplacement.same(loads.get(oldId),old)||!NativeFaultedJobReplacement.same(feedFacts(oldId),p.get("pending_feeds")))throw fault("MATERIAL_RECORD_INVALID","Material continuation outcome lacks its exact current unknown intent");
        Map<String,Object> retained=freeze(p);return ()->{loads.put(newId,copy(fresh));continuationOutcomes.put(receipt,retained);retiredLoads.putIfAbsent(oldId,receipt);String ancestor=previousId;Set<String> seen=new HashSet<>();while(!ancestor.equals(oldId)&&seen.add(ancestor)){retiredLoads.putIfAbsent(ancestor,receipt);Map<String,Object> found=null;for(Map<String,Object> candidate:continuationIntents.values())if(Objects.equals(objectUnchecked(candidate.get("new_load")).get("load_id"),ancestor)){found=candidate;break;}if(found==null)break;ancestor=(String)found.get("previous_load_id");}refresh();};
    }
    /** Adopt only the already completed, still untouched new tray under a fresh local permit.
     * This writes a separate disposition; it performs no setter, save, load or binding mutation. */
    Map<String,Object> adoptCompletedReplacement(NativeFaultedJobReplacement.ContinuationPermit permit,String oldLoadId)throws Exception {
        owner();if(continuationAdoptionFaulted||continuationMutationFaulted)throw fault("MATERIAL_CONTINUATION_UNCERTAIN","Material adoption publication is uncertain; preserve history and verify it in a new journal replay instance");if(machine.isEnabled())throw fault("MACHINE_ENABLED","Disable before adopting a completed simulator tray replacement");empty();
        Map<String,Object> authority=permit.authorizeMaterial(this,oldLoadId),captured=object(authority.get("phase_row"));
        exact(authority,"continuation_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","continuation_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","phase_row");
        if(!Objects.equals(permit.continuationId(),authority.get("continuation_id"))||!Objects.equals(permit.attemptId(),authority.get("replacement_attempt_id")))throw fault("MATERIAL_CONTINUATION_SCOPE","Continuation permit identity differs from its authority");
        Map<String,Object> row=completedAdoptionRow(text(authority.get("original_recovery_operation_id")),text(authority.get("original_fault_set_sha256")),oldLoadId);
        boolean chained=row.containsKey("continuation_chain");if(chained?!NativeFaultedJobReplacement.continuationPhaseDigest(captured).equals(NativeFaultedJobReplacement.continuationPhaseDigest(row)):!NativeFaultedJobReplacement.same(captured,row))throw fault("MATERIAL_CONTINUATION_SCOPE","Completed material phase changed since the local continuation capture");
        Map<String,Object> parentDescriptor=object(row.get(chained?"effective_parent_outcome":"parent_outcome")),parent=object(parentDescriptor.get("payload")),fresh=object(parent.get("new_load"));String feeder=text(row.get("feeder_id"));
        boolean restartBindingUsed=!bindings.containsKey(feeder);Binding retained=restartBindingUsed?restartBinding(permit,authority,oldLoadId,row):binding(feeder);ReferenceTrayFeeder nativeFeeder=selected(feeder);Map<String,Object> observed=describe(nativeFeeder);
        if(nativeFeeder!=retained.feeder||!Objects.equals(fresh.get("part_id"),observed.get("part_id"))||!Objects.equals(fresh.get("geometry_sha256"),observed.get("geometry_sha256"))||integer(observed.get("native_feed_count"),0,10000)!=0||!NativeFaultedJobReplacement.same(fresh.get("capacity"),observed.get("capacity")))throw fault("MATERIAL_MODEL_CHANGED","Completed replacement no longer has its exact native tray and untouched counter");
        Map<String,Object> receipt=map("schema_version",chained?2:1,"receipt_id",UUID.randomUUID().toString(),"continuation_id",authority.get("continuation_id"),"replacement_attempt_id",authority.get("replacement_attempt_id"),"recovery_operation_id",authority.get("recovery_operation_id"),"fault_set_sha256",authority.get("fault_set_sha256"),"continuation_capture_sha256",authority.get("continuation_capture_sha256"),"original_recovery_operation_id",authority.get("original_recovery_operation_id"),"original_fault_set_sha256",authority.get("original_fault_set_sha256"),
            "old_load_id",oldLoadId,"phase_row_sha256",chained?NativeFaultedJobReplacement.continuationPhaseDigest(row):NativeFaultedJobReplacement.digest(captured),"parent_outcome_id",parent.get("receipt_id"),"parent_outcome_sha256",NativeFaultedJobReplacement.digest(parent),"adopted_load",fresh,"old_load_sha256",NativeFaultedJobReplacement.digest(object(row.get("old_load"))),"old_feed_facts_sha256",NativeFaultedJobReplacement.digest(object(row.get("old_feed_facts"))),"material_setup_revision",revision(),
            "native_observation",map("feeder_id",feeder,"part_id",observed.get("part_id"),"geometry_sha256",observed.get("geometry_sha256"),"native_feed_count",observed.get("native_feed_count"),"retained_binding_matches_current_objects",true,"machine_disabled",true,"nozzles_empty",true,"observation","current-native-simulator-model-on-owning-executor","physical_inventory_verified",false),
            "original_outcomes_preserved",true,"execution_authority_restored",false,"physical_inventory_verified",false);
        if(chained)receipt.put("parent_outcome_type",parentDescriptor.get("type"));
        // Reject known local conflicts before entering the append boundary. Once append starts,
        // every exceptional return is conservatively uncertain even if the sink failed before force.
        prepareContinuationAdoption("material_continuation_adoption",receipt);permit.check();try{sink.append("material_continuation_adoption",receipt);Map<String,Object> committed=continuationAdoptions.get(receipt.get("receipt_id"));if(committed==null)throw fault("MATERIAL_RECORD_INVALID","Material adoption append returned without a committed receipt");if(restartBindingUsed){permit.check();restartBinding(permit,authority,oldLoadId,row);bindings.put(feeder,retained);refresh();}return committed;}
        catch(Exception|Error failure){continuationAdoptionFaulted=true;refresh();throw failure;}
    }
    /** Current reducer facts for one completed target. Other pending replacement steps remain
     * unresolved and are neither adopted nor retried by this target's separate receipt. */
    private Map<String,Object> completedAdoptionRow(String originalOperation,String originalFault,String oldId)throws Exception {
        uuid(originalOperation);uuid(oldId);digestText(originalFault);if(!changes.isEmpty())throw fault("MATERIAL_OUTCOME_UNKNOWN","An ordinary tray change remains unresolved");
        Map<String,Object> row=replacementProgressRow(originalOperation,originalFault,oldId,originalParent(originalOperation,originalFault,oldId));boolean chained=row.containsKey("continuation_chain");if(!"completed".equals(row.getOrDefault("effective_phase",row.get("phase"))))throw fault("MATERIAL_OUTCOME_UNKNOWN","Target replacement has no terminal outcome");
        Map<String,Object> parent=object(object(row.get(chained?"effective_parent_outcome":"parent_outcome")).get("payload")),fresh=object(parent.get("new_load"));String newId=uuid(fresh.get("load_id")),feeder=text(fresh.get("feeder_id"));
        if(!Objects.equals(active.get(feeder),newId)||!NativeFaultedJobReplacement.same(loads.get(newId),fresh)||!"loaded".equals(fresh.get("state"))||!feedFacts(newId).isEmpty())throw fault("MATERIAL_CONTINUATION_SCOPE","Completed replacement no longer binds exact active load and preserved original feed facts");integer(fresh.get("initial_index"),0,0);integer(fresh.get("current_index"),0,0);integer(fresh.get("observed_advances"),0,0);return row;
    }
    private Runnable prepareContinuationAdoption(String type,Map<String,Object> source)throws Exception {
        if(!"material_continuation_adoption".equals(type))throw fault("MATERIAL_RECORD_INVALID","Unknown material continuation record");
        Map<String,Object> p=copy(source);int version=integer(p.get("schema_version"),1,2);List<String> keys=new ArrayList<>(List.of("schema_version","receipt_id","continuation_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","continuation_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","old_load_id","phase_row_sha256","parent_outcome_id","parent_outcome_sha256","adopted_load","old_load_sha256","old_feed_facts_sha256","material_setup_revision","native_observation","original_outcomes_preserved","execution_authority_restored","physical_inventory_verified"));if(version==2)keys.add("parent_outcome_type");exact(p,keys.toArray(new String[0]));
        String receipt=uuid(p.get("receipt_id")),continuation=uuid(p.get("continuation_id")),oldId=uuid(p.get("old_load_id"));uuid(p.get("replacement_attempt_id"));uuid(p.get("recovery_operation_id"));uuid(p.get("original_recovery_operation_id"));uuid(p.get("parent_outcome_id"));
        for(String key:List.of("fault_set_sha256","continuation_capture_sha256","original_fault_set_sha256","phase_row_sha256","parent_outcome_sha256","old_load_sha256","old_feed_facts_sha256"))digestText(p.get(key));
        if(Objects.equals(p.get("recovery_operation_id"),p.get("original_recovery_operation_id"))||!Boolean.TRUE.equals(p.get("original_outcomes_preserved"))||!Boolean.FALSE.equals(p.get("execution_authority_restored"))||!Boolean.FALSE.equals(p.get("physical_inventory_verified"))||!revision().equals(p.get("material_setup_revision")))throw fault("MATERIAL_RECORD_INVALID","Material adoption has an invalid fresh operation or scope");
        if(continuationAdoptions.size()>=MAX_CONTINUATION_ADOPTIONS)throw fault("MATERIAL_CAPACITY","Retained material continuation receipt limit reached");
        if(continuationAdoptions.containsKey(receipt)||replacementReceipts.containsKey(receipt)||replacementIntents.containsKey(receipt)||continuationIntents.containsKey(receipt))throw fault("MATERIAL_CONTINUATION_REPEATED","Material continuation receipt identity already exists or collides with an original replacement receipt");
        for(Map<String,Object> prior:continuationAdoptions.values())if(Objects.equals(continuation,prior.get("continuation_id"))&&Objects.equals(oldId,prior.get("old_load_id")))throw fault("MATERIAL_CONTINUATION_REPEATED","This continuation already adopted the completed material step");
        Map<String,Object> row=completedAdoptionRow((String)p.get("original_recovery_operation_id"),(String)p.get("original_fault_set_sha256"),oldId);boolean chained=row.containsKey("continuation_chain");if(chained!=(version==2))throw fault("MATERIAL_RECORD_INVALID","Material adoption schema differs from its parent generation");Map<String,Object> descriptor=object(row.get(chained?"effective_parent_outcome":"parent_outcome")),parent=object(descriptor.get("payload")),fresh=object(parent.get("new_load"));if(version==2&&!Objects.equals(descriptor.get("type"),p.get("parent_outcome_type")))throw fault("MATERIAL_RECORD_INVALID","Adoption parent event type differs");
        if(!Objects.equals(parent.get("receipt_id"),p.get("parent_outcome_id"))||!NativeFaultedJobReplacement.digest(parent).equals(p.get("parent_outcome_sha256"))||!NativeFaultedJobReplacement.same(fresh,p.get("adopted_load"))||!NativeFaultedJobReplacement.digest(object(row.get("old_load"))).equals(p.get("old_load_sha256"))||!NativeFaultedJobReplacement.digest(object(row.get("old_feed_facts"))).equals(p.get("old_feed_facts_sha256")))throw fault("MATERIAL_RECORD_INVALID","Material adoption differs from the exact completed parent and original history");
        Map<String,Object> observed=object(p.get("native_observation"));exact(observed,"feeder_id","part_id","geometry_sha256","native_feed_count","retained_binding_matches_current_objects","machine_disabled","nozzles_empty","observation","physical_inventory_verified");
        for(String key:List.of("feeder_id","part_id","geometry_sha256"))if(!Objects.equals(fresh.get(key),observed.get(key)))throw fault("MATERIAL_RECORD_INVALID","Material adoption native observation differs from completed load");
        integer(observed.get("native_feed_count"),0,0);if(!Boolean.TRUE.equals(observed.get("retained_binding_matches_current_objects"))||!Boolean.TRUE.equals(observed.get("machine_disabled"))||!Boolean.TRUE.equals(observed.get("nozzles_empty"))||!Boolean.FALSE.equals(observed.get("physical_inventory_verified"))||!"current-native-simulator-model-on-owning-executor".equals(observed.get("observation")))throw fault("MATERIAL_RECORD_INVALID","Material adoption lacks exact native prerequisites");
        // phase_row_sha256 and common authority are cross-checked against the coordinator's
        // forced continuation intent before append and on replay. Transient bindings are never replayed.
        Map<String,Object> retained=freeze(p);return ()->{continuationAdoptions.put(receipt,retained);refresh();};
    }
    private static String digestText(Object value)throws Bridge.Fault {String digest=text(value);if(!digest.matches("[a-f0-9]{64}"))throw fault("MATERIAL_RECORD_INVALID","Exact SHA-256 digest required");return digest;}
    private Map<String,Object> feedFacts(String loadId){Map<String,Object> result=new TreeMap<>();for(Map.Entry<String,Map<String,Object>> e:feeds.entrySet())if(loadId.equals(objectUnchecked(e.getValue().get("material_load")).get("load_id")))result.put(e.getKey(),e.getValue());return freeze(result);}
    private Runnable prepareReplacement(String type,Map<String,Object> source)throws Exception {
        if(!Set.of("material_replacement_intent","material_replacement_outcome").contains(type))throw fault("MATERIAL_RECORD_INVALID","Unknown replacement event");
        Map<String,Object> p=copy(source);exact(p,"schema_version","receipt_id","recovery_operation_id","fault_set_sha256","revision","old_load_id","old_load","pending_feeds","native_index_before","new_load","old_disposition","physical_inventory_verified");integer(p.get("schema_version"),1,1);String receipt=uuid(p.get("receipt_id")),oldId=uuid(p.get("old_load_id"));uuid(p.get("recovery_operation_id"));if(!text(p.get("fault_set_sha256")).matches("[a-f0-9]{64}")||!Boolean.FALSE.equals(p.get("physical_inventory_verified"))||!"consumption_unknown_or_consumed_preserved".equals(p.get("old_disposition")))throw fault("MATERIAL_RECORD_INVALID","Invalid replacement scope");
        Map<String,Object> old=object(p.get("old_load")),fresh=object(p.get("new_load"));exact(fresh,"schema_version","load_id","feeder_id","part_id","geometry_sha256","capacity","initial_index","current_index","observed_advances","state","created_at");integer(fresh.get("schema_version"),1,1);String newId=uuid(fresh.get("load_id")),feeder=text(fresh.get("feeder_id"));Instant.parse(text(fresh.get("created_at")));
        for(String key:List.of("feeder_id","part_id","geometry_sha256","capacity"))if(!NativeFaultedJobReplacement.same(old.get(key),fresh.get(key)))throw fault("MATERIAL_RECORD_INVALID","Replacement changed tray identity/geometry");
        integer(fresh.get("initial_index"),0,0);integer(fresh.get("current_index"),0,0);integer(fresh.get("observed_advances"),0,0);integer(p.get("native_index_before"),0,integer(old.get("capacity"),1,10000));int rev=integer(p.get("revision"),1,MAX_LOADS);
        if(type.endsWith("intent")){
            if(!changes.isEmpty()||hasCurrentReplacementIntent()||retiredLoads.containsKey(oldId)||replacementReceipts.containsKey(receipt)||loads.containsKey(newId)||newId.equals(oldId)||!NativeFaultedJobReplacement.same(old,loads.get(oldId))||!Objects.equals(active.get(feeder),oldId)||rev!=revision+1||!NativeFaultedJobReplacement.same(feedFacts(oldId),p.get("pending_feeds"))||!"loading_unknown".equals(fresh.get("state")))throw fault("MATERIAL_RECORD_INVALID","Replacement does not bind exact current old tray and feed facts");
            if(loads.size()>=MAX_LOADS)throw fault("MATERIAL_CAPACITY","Replacement capacity reached");
            return ()->{revision=rev;replacementIntents.put(receipt,freeze(p));loads.put(newId,copy(fresh));active.put(feeder,newId);bindings.remove(feeder);refresh();};
        }
        Map<String,Object> pending=replacementIntents.get(receipt),comparison=copy(p);object(comparison.get("new_load")).put("state","loading_unknown");
        if(pending==null||continuationSupersededBy.containsKey(receipt)||!Objects.equals(active.get(feeder),newId)||!NativeFaultedJobReplacement.same(loads.get(newId),pending.get("new_load"))||!NativeFaultedJobReplacement.same(comparison,pending)||!"loaded".equals(fresh.get("state"))||!NativeFaultedJobReplacement.same(old,loads.get(oldId))||!NativeFaultedJobReplacement.same(feedFacts(oldId),p.get("pending_feeds")))throw fault("MATERIAL_RECORD_INVALID","Replacement outcome has no exact active, unsuperseded original intent/history");
        return ()->{loads.put(newId,copy(fresh));retiredLoads.put(oldId,receipt);replacementReceipts.put(receipt,freeze(p));replacementIntents.remove(receipt);refresh();};
    }
    public void recoverEvent(String type,Map<String,Object> payload)throws Exception{prepareEvent(type,payload).run();}
    public void finishRecovery(){recovered=!loads.isEmpty();bindings.clear();pendingWitnesses.clear();restartWitnesses.clear();refresh();}
    private Map<String,Object> view(String id){Map<String,Object> out=copy(loads.get(id));String feeder=(String)out.get("feeder_id");out.put("active",id.equals(active.get(feeder)));out.put("retired_by",retiredLoads.get(id));out.put("retirement_disposition",retiredLoads.containsKey(id)?"consumption_unknown_or_consumed_preserved":null);out.put("remaining_configured_slots",((Number)out.get("capacity")).intValue()-((Number)out.get("current_index")).intValue());out.put("native_authority",bindings.containsKey(feeder)&&id.equals(active.get(feeder))&&changes.isEmpty()&&!hasCurrentReplacementIntent()&&!hasUnretiredFeeds());out.put("authority_observation","retained-binding-only; model revalidated at effect admission");out.put("physical_inventory_verified",false);return out;}
    private void refresh(){List<Object> rows=new ArrayList<>();for(String id:loads.keySet())rows.add(view(id));snapshot=freeze(map("material_setup_revision",revision(),"loads",rows,"pending_changes",new ArrayList<>(changes.values()),"pending_feeds",new ArrayList<>(feeds.values()),"replacement_intents",new ArrayList<>(replacementIntents.values()),"replacement_receipts",new ArrayList<>(replacementReceipts.values()),"restart_observation_receipts",new ArrayList<>(restartObservations.values()),"restart_observation_publication_uncertain",restartPublicationFaulted,"continuation_adoption_receipts",new ArrayList<>(continuationAdoptions.values()),"continuation_adoption_publication_uncertain",continuationAdoptionFaulted,"continuation_mutation_intents",new ArrayList<>(continuationIntents.values()),"continuation_mutation_outcomes",new ArrayList<>(continuationOutcomes.values()),"continuation_superseded_by",continuationSupersededBy,"current_unresolved_replacement_intents",currentReplacementIntents(),"current_unresolved_continuation_intents",currentContinuationIntents(),"continuation_mutation_publication_uncertain",continuationMutationFaulted,"available_trays",inventory,"available_trays_observed_at",inventoryObservedAt,"observed_at",Instant.now().toString(),"recovered_from_journal",recovered,"native_authority_restored",false,"authority","native-simulator","physical_inventory_verified",false,"limits",map("retained_loads",MAX_LOADS,"continuation_adoptions",MAX_CONTINUATION_ADOPTIONS,"slots_per_tray",10000),"scope","same-part-normal-full-tray-only"));}
    public static Map<String,Object> describe(ReferenceTrayFeeder f)throws Exception{
        if(f.getClass()!=ReferenceTrayFeeder.class||f.getFeedOptions()!=org.openpnp.machine.reference.ReferenceFeeder.FeedOptions.Normal)throw fault("MATERIAL_PROFILE_UNSUPPORTED","Exact ReferenceTrayFeeder with Normal feed options required");
        Part part=f.getPart();if(part==null||part.getPackage()==null)throw fault("MATERIAL_PROFILE_UNSUPPORTED","Configured native part/package required");
        int x=f.getTrayCountX(),y=f.getTrayCountY();if(x<1||x>100||y<1||y>100||x*y>10000||f.getFeedCount()<0||f.getFeedCount()>x*y)throw fault("MATERIAL_PROFILE_UNSUPPORTED","Finite explicit tray dimensions and index required");
        return map("feeder_id",text(f.getId()),"part_id",text(part.getId()),"geometry_sha256",fingerprint(f),"capacity",x*y,"native_feed_count",f.getFeedCount(),"supported",true,"physical_inventory_verified",false);
    }
    private static String fingerprint(ReferenceTrayFeeder f)throws Exception{return fingerprint(f,false);}
    private static String fingerprint(ReferenceTrayFeeder f,boolean completedRead)throws Exception{
        Part p=f.getPart();org.openpnp.model.Package pkg=p.getPackage();Footprint fp=pkg.getFootprint();if(fp==null||fp.getPads().size()>512)throw fault("MATERIAL_PROFILE_UNSUPPORTED","Bounded package footprint required");
        List<Object> pads=new ArrayList<>();for(Footprint.Pad pad:fp.getPads())pads.add(map("name",pad.getName(),"x",finite(pad.getX()),"y",finite(pad.getY()),"width",finite(pad.getWidth()),"height",finite(pad.getHeight()),"rotation",finite(pad.getRotation()),"roundness",finite(pad.getRoundness()),"mark",pad.getMark()));
        List<String> tips=new ArrayList<>();for(NozzleTip tip:completedRead?cachedCompatibleTips(pkg):pkg.getCompatibleNozzleTips())tips.add(text(tip.getId()));Collections.sort(tips);
        Map<String,Object> model=map("class",f.getClass().getName(),"id",text(f.getId()),"part_id",text(p.getId()),"package_id",text(pkg.getId()),"location",pose(f.getLocation()),"offsets",pose(f.getOffsets()),"count_x",f.getTrayCountX(),"count_y",f.getTrayCountY(),"feed_options",f.getFeedOptions().name(),"feed_retry",f.getFeedRetryCount(),"pick_retry",f.getPickRetryCount(),"part_pick_retry",p.getPickRetryCount(),"part_speed",finite(p.getSpeed()),"part_height",length(p.getHeight()),"part_through_board",length(p.getThroughBoardDepth()),"footprint",map("units",fp.getUnits().name(),"width",finite(fp.getBodyWidth()),"height",finite(fp.getBodyHeight()),"pads",pads),"tips",tips,"vacuum",finite(pkg.getPickVacuumLevel()),"blowoff",finite(pkg.getPlaceBlowOffLevel()));
        byte[] bytes=GSON.toJson(freeze(model)).getBytes(StandardCharsets.UTF_8);if(bytes.length>262144)throw fault("MATERIAL_PROFILE_UNSUPPORTED","Fingerprint limit");byte[] hash=MessageDigest.getInstance("SHA-256").digest(bytes);StringBuilder s=new StringBuilder();for(byte b:hash)s.append(String.format("%02x",b&255));return s.toString();
    }
    /** Pinned cache read: final validation never populates Package's compatible-tip cache. */
    private static List<NozzleTip> cachedCompatibleTips(org.openpnp.model.Package pkg)throws Exception {
        java.lang.reflect.Field field=org.openpnp.model.Package.class.getDeclaredField("compatibleNozzleTips");if(!field.trySetAccessible())throw fault("MATERIAL_MODEL_CHANGED","Pinned compatible-tip cache is unavailable");Object raw=field.get(pkg);
        if(!(raw instanceof Set))throw fault("MATERIAL_MODEL_CHANGED","Final publication validation requires the previously initialized compatible-tip cache");
        List<NozzleTip> tips=new ArrayList<>();for(Object value:(Set<?>)raw){if(!(value instanceof NozzleTip))throw fault("MATERIAL_MODEL_CHANGED","Invalid cached compatible nozzle-tip identity");NozzleTip tip=(NozzleTip)value;if(Configuration.get().getMachine().getNozzleTip(tip.getId())!=tip)throw fault("MATERIAL_MODEL_CHANGED","Foreign cached compatible nozzle-tip identity");tips.add(tip);}return tips;
    }
    private static Object length(Length x){return x==null?null:map("value",finite(x.getValue()),"units",x.getUnits().name());}
    private static Object pose(Location p){return map("units",p.getUnits().name(),"x",finite(p.getX()),"y",finite(p.getY()),"z",finite(p.getZ()),"rotation",finite(p.getRotation()));}
    private static double finite(double x){if(!Double.isFinite(x))throw new IllegalArgumentException("Nonfinite native geometry");return x;}
    private static String text(Object x)throws Bridge.Fault{if(!(x instanceof String)||((String)x).isBlank()||((String)x).length()>256||((String)x).chars().anyMatch(c->c<32||c==127))throw fault("MATERIAL_RECORD_INVALID","Invalid bounded text");return (String)x;}
    private static String uuid(Object x)throws Bridge.Fault{String s=text(x);try{if(!UUID.fromString(s).toString().equals(s))throw new IllegalArgumentException();}catch(Exception e){throw fault("MATERIAL_RECORD_INVALID","Canonical UUID required");}return s;}
    private static int integer(Object x,int min,int max)throws Bridge.Fault{if(!(x instanceof Number))throw fault("MATERIAL_RECORD_INVALID","Number required");try{int n=new BigDecimal(x.toString()).intValueExact();if(n<min||n>max)throw new ArithmeticException();return n;}catch(ArithmeticException e){throw fault("MATERIAL_RECORD_INVALID","Integral bounded count required");}}
    private static Map<String,Object> object(Object x)throws Bridge.Fault{if(!(x instanceof Map))throw fault("MATERIAL_RECORD_INVALID","Object required");return (Map<String,Object>)x;}
    private static void exact(Map<String,Object> m,String...keys)throws Bridge.Fault{if(!m.keySet().equals(new HashSet<>(Arrays.asList(keys))))throw fault("MATERIAL_RECORD_INVALID","Unknown or missing fields");}
    private static Bridge.Fault fault(String code,String message){return new Bridge.Fault(code,message);}
    private static Map<String,Object> map(Object...x){Map<String,Object> m=new LinkedHashMap<>();for(int i=0;i<x.length;i+=2)m.put((String)x[i],x[i+1]);return m;}
    private static Map<String,Object> copy(Map<String,Object> m){return (Map<String,Object>)copyValue(m,false);}
    private static Map<String,Object> freeze(Map<String,Object> m){return (Map<String,Object>)copyValue(m,true);}
    private static Object copyValue(Object x,boolean immutable){if(x instanceof Map){Map<String,Object> m=new TreeMap<>();((Map<?,?>)x).forEach((k,v)->m.put((String)k,copyValue(v,immutable)));return immutable?Collections.unmodifiableMap(m):m;}if(x instanceof Collection){List<Object> l=new ArrayList<>();for(Object v:(Collection<?>)x)l.add(copyValue(v,immutable));return immutable?Collections.unmodifiableList(l):l;}if(x==null||x instanceof String||x instanceof Boolean||x instanceof Number)return x;throw new IllegalArgumentException("Non-DTO value");}
}
