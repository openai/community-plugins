/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.awt.geom.AffineTransform;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.model.Abstract2DLocatable.Side;
import org.openpnp.util.IdentifiableList;

/**
 * Bounded, job-local native placement updates. Stage is read-only. Apply builds a detached
 * native tree and replaces the root child list in one setter; the Job's opaque history maps
 * remain intact. No source definitions/files, motion, feeders or calibration routines are edited.
 * Caller MUST hold the native executor/GUI owner and revision fence for stage and apply. Any
 * commit exception requires a caller fault fence, even if the old list is restored successfully.
 */
public final class NativePlacementEdits {
    private NativePlacementEdits() { }
    public static final int MAX_CHANGES=1000, MAX_LOCATIONS=5000, MAX_DEFINITIONS=1000,
        MAX_EXPANDED_PLACEMENTS=10000, MAX_RECORDS=100000, MAX_DEPTH=8;
    private static final Gson GSON=new Gson();
    private static final String DELIMITER=PlacementsHolderLocation.ID_DELIMITTER;

    public static final class Fault extends Exception {
        public final String code;
        Fault(String code,String message){super(message);this.code=code;}
        Fault(String code,String message,Throwable cause){super(message,cause);this.code=code;}
    }

    /** Read every expanded real/pseudo record, including X-outs and the opposite side.
     * The caller holds the native executor and revision fence. No lazy transform getter is used.
     * Pages expose a semantic fingerprint so clients can reject a changing native graph.
     */
    public static Map<String,Object> inspect(Configuration configuration,Job job,int offset,int limit)throws Exception {
        return inspect(configuration,job,offset,limit,false);
    }

    /** Read-only inspection may retain native solder-paste pad values and identities.
     * This profile never admits pads to an edit, copy or publication path. */
    public static Map<String,Object> inspectForLoadedBoardInspection(Configuration configuration,Job job,int offset,int limit)throws Exception {
        return inspect(configuration,job,offset,limit,true);
    }

    private static Map<String,Object> inspect(Configuration configuration,Job job,int offset,int limit,boolean readOnlyPads)throws Exception {
        if(configuration==null||configuration!=Configuration.get()||job==null)fail("NO_JOB","The current native Configuration and Job are required");
        if(offset<0||offset>MAX_EXPANDED_PLACEMENTS||limit<1||limit>200)fail("INSPECTION_PAGE_RANGE","Offset must be0..10000 and limit1..200");
        Graph graph=new Graph(configuration,job,readOnlyPads);List<Map<String,Object>> rows=new ArrayList<>();int index=0;
        for(Node node:graph.nodes){
            List<Placement> records=new ArrayList<>(node.source.getPlacementsHolder().getPlacements());
            int ordinaryRecords=records.size(),localIndex=0;
            if(node.source.getClass()==PanelLocation.class)records.addAll(((PanelLocation)node.source).getPanel().getPseudoPlacements());
            for(Placement placement:records){
                // Native Panel copies represent derived pseudo instances as plain Placement.
                boolean pseudo=localIndex++>=ordinaryRecords;
                if(index>=offset&&rows.size()<limit){
                    Map<String,Object> row=new LinkedHashMap<>(new Value(placement).dto());
                    Map<String,Object> location=pose(placement.getLocation().convertToUnits(LengthUnit.Millimeters));location.put("units","mm");row.put("location",location);
                    row.put("holder_instance_id",node.id);row.put("root_instance_id",node.id.split(DELIMITER,2)[0]);row.put("placement_id",placement.getId());
                    row.put("holder_kind",node.source.getClass()==BoardLocation.class?"board":"panel");
                    row.put("holder_local_side",node.source.getSide());row.put("holder_global_side",node.source.getGlobalSide());
                    row.put("holder_locally_enabled",node.source.isLocallyEnabled());row.put("holder_effectively_enabled",node.source.isEnabled());
                    row.put("placed",job.retrievePlacedStatus(node.source,placement.getId()));row.put("derived",pseudo);row.put("read_only",pseudo);
                    row.put("ordinary_active_side_placement",!pseudo&&node.source.getClass()==BoardLocation.class&&node.source.isEnabled()&&placement.isEnabled()&&placement.getType()==Placement.Type.Placement&&placement.getSide()==node.source.getGlobalSide());
                    rows.add(row);
                }
                index++;
            }
        }
        if(offset>index)fail("INSPECTION_PAGE_RANGE","Offset exceeds the current record count");
        return values("source_fingerprint",graph.fingerprint,"total_records",index,"offset",offset,"limit",limit,"count",rows.size(),"next_offset",offset+rows.size(),"eof",offset+rows.size()==index,"records",rows,
            "coordinate_frame","holder","location_units","mm","rotation_units","degrees","side_effects_performed",false,"hardware_qualified",false,
            "limits",values("records",MAX_EXPANDED_PLACEMENTS,"page_records",200,"nesting",MAX_DEPTH));
    }

    /** Immutable input specs and preview; no native clone or property mutation occurs here. */
    public static Patch stage(Configuration configuration,Job job,JsonArray changes)throws Exception {
        if(configuration==null||configuration!=Configuration.get()||job==null)fail("NO_JOB","The current native Configuration and Job are required");
        Graph graph=new Graph(configuration,job);
        if(changes==null||changes.size()<1||changes.size()>MAX_CHANGES)fail("EDIT_LIMIT","Provide 1..1000 typed placement changes");
        IdentityHashMap<Placement,Edit> shared=new IdentityHashMap<>(),instances=new IdentityHashMap<>();
        List<Map<String,Object>> previews=new ArrayList<>();Set<Placement> touched=identitySet();
        for(JsonElement raw:changes){
            JsonObject row=object(raw,"change");fields(row,"scope","holder_instance_id","placement_id","set");
            String scope=string(row,"scope",64),holderId=string(row,"holder_instance_id",2048),ref=string(row,"placement_id",128);
            if(!scope.equals("job_instance")&&!scope.equals("job_shared_definition"))fail("EDIT_SCOPE","Expected job_instance or job_shared_definition");
            Node node=graph.byId.get(holderId);if(node==null)fail("HOLDER_NOT_FOUND","No exact native holder instance: "+holderId);
            Placement placement=node.source.getPlacementsHolder().getPlacements().get(ref);
            if(placement==null){
                if(node.source.getClass()==PanelLocation.class)for(Placement pseudo:((PanelLocation)node.source).getPanel().getPseudoPlacements())if(Objects.equals(ref,pseudo.getId()))fail("PSEUDO_PLACEMENT_DERIVED","Edit the underlying real placement; pseudo placements are derived native alignment records");
                fail("PLACEMENT_NOT_FOUND","No exact native placement reference: "+ref);
            }
            boolean all=scope.equals("job_shared_definition");Placement definition=placement.getDefinition();
            JsonObject set=object(row.get("set"),"set");Edit edit=new Edit(configuration,set);
            List<Node> targets=new ArrayList<>();for(Node candidate:graph.nodes)if(candidate==node||(all&&candidate.source.getPlacementsHolder().getDefinition()==node.source.getPlacementsHolder().getDefinition()))targets.add(candidate);
            Value definitionBefore=new Value(definition),definitionAfter=all?edit.apply(definitionBefore):definitionBefore;
            List<Map<String,Object>> affected=new ArrayList<>();boolean anyChanged=all&&!definitionBefore.same(definitionAfter);
            for(Node target:targets){
                Placement current=target.source.getPlacementsHolder().getPlacements().get(ref);
                if(current==null||current.getDefinition()!=definition)fail("DEFINITION_CONFLICT","Native placement definition linkage is inconsistent");
                if(!touched.add(current))fail("OVERLAPPING_EDITS","A placement is targeted more than once; combine its fields in one row");
                Value before=new Value(current),after=edit.apply(before);validateValue(configuration,after);
                if(target.source.getClass()==PanelLocation.class&&after.type!=Placement.Type.Fiducial)fail("PANEL_PLACEMENT_TYPE","Panel-owned real records must be fiducials");
                boolean changed=!before.same(after);boolean placed=job.retrievePlacedStatus(target.source,ref);
                if(placed&&changed)fail("PLACED_HISTORY_CONFLICT","A placed record cannot be edited without an explicit separate physical-history reconciliation: "+target.id+DELIMITER+ref);
                anyChanged|=changed;affected.add(values("holder_instance_id",target.id,"placement_id",ref,"placed",placed,"changed",changed,"before",before.dto(),"after",after.dto()));
            }
            if(all){if(shared.put(definition,edit)!=null)fail("OVERLAPPING_EDITS","Shared placement definition is edited twice");}
            else instances.put(placement,edit);
            previews.add(values("scope",scope,"anchor_holder_instance_id",holderId,"placement_id",ref,"changed",anyChanged,"definition_before",definitionBefore.dto(),"definition_after",definitionAfter.dto(),"affected",affected));
        }
        boolean changed=false;for(Map<String,Object> row:previews)changed|=(Boolean)row.get("changed");
        Map<String,Object> preview=values("valid",true,"changed",changed,"changes",previews,"source_fingerprint",graph.fingerprint,"affected_root_ids",graph.rootIds(),
            "scope","current-job-only","native_ids_preserved",true,"opaque_placed_history_preserved",true,
            "other_jobs_and_source_definitions_modified",false,"instance_edits_clone_required_ancestor_definitions",true,
            "derived_pseudo_placements_rebuilt",true,"registration_invalidated",changed,"production_validation_required",changed,
            "side_effects_performed",false,"hardware_qualified",false,"unsupported",Arrays.asList("add-remove-rename-placement","arbitrary-native-subclasses","custom-outlines","solder-paste-pads","direct-pseudo-edits","machine-frame-input","physical-history-reset"));
        return new Patch(configuration,job,graph,shared,instances,preview,changed);
    }

    public static final class Patch {
        private final Configuration config;private final Job job;private final Graph graph;
        private final IdentityHashMap<Placement,Edit> shared,instances;private final String previewJson;private final boolean changed;
        private boolean consumed;
        private Patch(Configuration c,Job j,Graph g,IdentityHashMap<Placement,Edit>s,IdentityHashMap<Placement,Edit>i,Map<String,Object>p,boolean changed){config=c;job=j;graph=g;shared=s;instances=i;previewJson=GSON.toJson(p);this.changed=changed;}
        public JsonObject preview(){return new JsonParser().parse(previewJson).getAsJsonObject();}
        /** Single use. No admission/revision/ownership permission is implied by this helper. */
        public synchronized Map<String,Object> apply()throws Exception {
            if(consumed)fail("PLAN_CONSUMED","Placement edit plan was already applied or attempted");
            if(config!=Configuration.get())fail("STALE_PLACEMENT_PLAN","The native Configuration instance changed after preview");
            Graph current=new Graph(config,job);
            if(!graph.sameIdentities(current)||!graph.fingerprint.equals(current.fingerprint))fail("STALE_PLACEMENT_PLAN","Native graph, placement history or inputs changed after preview");
            for(Edit edit:shared.values())edit.revalidatePart(config);for(Edit edit:instances.values())edit.revalidatePart(config);
            consumed=true;
            if(!changed)return values("applied",false,"changed",false,"job_identity_preserved",true,"hardware_qualified",false);
            Builder builder=new Builder(this);
            IdentifiableList<PlacementsHolderLocation<?>> candidates=new IdentifiableList<>();
            // All expensive construction and validation precedes the one publication setter.
            for(Node node:graph.roots){Spec spec=builder.spec(node);PlacementsHolderLocation<?> copy=builder.instantiate(spec);copy.setParent(job.getRootPanelLocation());if(copy.getClass()==PanelLocation.class)PanelLocation.setParentsOfAllDescendants((PanelLocation)copy);candidates.add(copy);}
            builder.verify(candidates);
            Panel root=job.getRootPanelLocation().getPanel();IdentifiableList<PlacementsHolderLocation<?>> old=new IdentifiableList<>();old.addAll(root.getChildren());
            boolean oldJobDirty=job.isDirty(),oldRootDirty=root.isDirty();
            try{for(PlacementsHolderLocation<?> child:candidates)child.addPropertyChangeListener(root);root.setChildren(candidates);job.setDirty(true);}
            catch(Throwable error){
                Throwable rollback=null;try{root.setChildren(old);for(PlacementsHolderLocation<?> child:candidates)if(child.isListener(root))child.removePropertyChangeListener(root);root.setDirty(oldRootDirty);job.setDirty(oldJobDirty);}catch(Throwable failure){rollback=failure;}
                Fault failure=new Fault(rollback==null?"PLACEMENT_COMMIT_FAILED":"PLACEMENT_ROLLBACK_FAILED","Native publication failed; the caller must fence further work and inspect the retained original job",error);if(rollback!=null)failure.addSuppressed(rollback);throw failure;
            }
            for(PlacementsHolderLocation<?> child:old)if(child.isListener(root))child.removePropertyChangeListener(root);
            retireOriginalInstances(graph);
            return values("applied",true,"changed",true,"job_identity_preserved",true,"native_ids_preserved",true,"affected_root_ids",graph.rootIds(),
                "opaque_placed_history_preserved",true,"registration_invalidated",true,"production_validation_required",true,
                "private_shared_definition_count",builder.base.size(),"private_instance_definition_count",builder.variants,
                "physical_qualification",false,"hardware_qualified",false);
        }
    }

    /** Common single-use plan view; adding this interface does not change placement behavior. */
    public interface StagedStructure {
        JsonObject preview();
        Map<String,Object> apply()throws Exception;
    }
    @FunctionalInterface public interface Publication { void publish()throws Exception; }
    /** The caller must freshly verify exact native executor, lease/epoch, revisions and lineage
     * under its publication authority, then invoke the supplied one-shot setter synchronously.
     * The model helper cannot establish external ownership. Never retain or defer Publication. */
    public interface PanelPublicationFence extends StructurePublicationFence {
        void publishIfCurrent(Configuration config,Job job,StructureState expected,Publication publication)throws Exception;
    }

    /** PRIVATE PROTOTYPE. This API is not exposed by the packaged bridge or MCP. */
    public interface StructureAuthority {
        /** Trusted caller must prove exact current lineage has never had native processor/action
         * admission, inspect complete current/retired load history, and hold executor/ownership.
         * Missing/incomplete proof must throw. This is not a client 'unexecuted' flag. */
        StructureState requireUnexecuted(Configuration config,Job job)throws Exception;
        /** Atomically compare expected lineage/revision, append AND force reservation, then advance
         * revision by one. Reserved logical IDs survive failure/restart; never delete to retry. */
        void reserveAndForce(StructureState expected,JsonObject reservation)throws Exception;
    }
    public interface StructurePublicationFence {
        /** After durable reservation, before root setter: revoke job validation/plans/registration.
         * Preserve every material fact/load identity. Failure fences the caller; no auto replay. */
        void invalidateBeforePublication(Job job,List<String> rootIds)throws Exception;
    }
    public static final class StructureState {
        public final String lineageId;public final long revision;public final Set<String> reservedLogicalIds;
        public StructureState(String id,long revision,Set<String> reserved)throws Exception {
            if(id==null||!id.matches("[a-zA-Z0-9_.-]{1,128}")||revision<1||revision==Long.MAX_VALUE||reserved==null||reserved.size()>100000)fail("STRUCTURE_AUTHORITY_INVALID","Invalid bounded caller lineage state");
            TreeSet<String> copy=new TreeSet<>();for(String key:reserved){if(key==null||key.length()>4096||key.indexOf('\0')>=0)fail("STRUCTURE_AUTHORITY_INVALID","Invalid reserved logical ID");copy.add(key);}
            lineageId=id;this.revision=revision;reservedLogicalIds=Collections.unmodifiableSet(copy);
        }
        boolean same(StructureState other){return other!=null&&lineageId.equals(other.lineageId)&&revision==other.revision&&reservedLogicalIds.equals(other.reservedLogicalIds);}
    }
    private static final class Membership {
        final Map<String,Value> additions=new LinkedHashMap<>();final Set<String> removals=new LinkedHashSet<>();
        void apply(Map<String,Value> values){for(String id:removals)values.remove(id);for(Map.Entry<String,Value> e:additions.entrySet())values.put(e.getKey(),new Value(e.getValue()));}
    }
    private static Map<String,Value> membership(PlacementsHolder<?> holder){Map<String,Value> rows=new LinkedHashMap<>();for(Placement p:holder.getPlacements())rows.put(p.getId(),new Value(p));return rows;}
    private static Value creation(Configuration config,JsonObject input)throws Exception {
        fields(input,"location","side","type","part_id","enabled","error_handling","comments","rank");
        if(input.entrySet().size()!=8)fail("STRUCTURE_FIELDS","Creation requires all eight explicit placement fields");
        Value v=new Value();v.location=parseLocation(input.get("location"));v.side=enumValue(Side.class,string(input,"side",16));v.type=enumValue(Placement.Type.class,string(input,"type",16));
        v.part=config.getPart(string(input,"part_id",128));if(v.part==null)fail("PART_NOT_FOUND","Creation requires an existing configured part");
        v.enabled=bool(input,"enabled");v.error=enumValue(Placement.ErrorHandling.class,string(input,"error_handling",16));v.comments=string(input,"comments",2048,true);v.rank=integer(input,"rank",-100000,100000);validateValue(config,v);return v;
    }
    private static StructureState structureAuthority(Configuration config,Job job,StructureAuthority authority)throws Exception {
        if(authority==null)fail("STRUCTURE_LINEAGE_REQUIRED","Complete trusted caller lineage authority is required");
        if(config!=Configuration.get()||job==null)fail("STALE_PLACEMENT_PLAN","Current native configuration/job required");
        if(config.getMachine().isEnabled())fail("STRUCTURE_MACHINE_ENABLED","Structure editing requires disabled native machine");
        for(org.openpnp.spi.Head head:config.getMachine().getHeads())for(org.openpnp.spi.Nozzle nozzle:head.getNozzles())if(nozzle.getPart()!=null)fail("STRUCTURE_HELD_PART","Structure editing requires empty native nozzles");
        try{if(!Job.hasBoardLoadHistoryApi())fail("STRUCTURE_HISTORY_UNAVAILABLE","Complete native history API required");}
        catch(NoSuchMethodError missing){fail("STRUCTURE_HISTORY_UNAVAILABLE","Complete native history API required");}
        if(!job.getPlacedStatusSnapshot().isEmpty())fail("STRUCTURE_HISTORY_PRESENT","Any native placed-status key, including false/orphan keys, prevents structure edits");
        StructureState state=authority.requireUnexecuted(config,job);if(state==null)fail("STRUCTURE_LINEAGE_REQUIRED","Caller did not provide complete lineage authority");return state;
    }
    public static StructurePatch stageStructure(Configuration config,Job job,JsonArray changes,StructureAuthority authority,StructurePublicationFence fence)throws Exception {
        if(fence==null)fail("STRUCTURE_FENCE_REQUIRED","Caller publication invalidation fence required");
        StructureState state=structureAuthority(config,job,authority);Graph graph=new Graph(config,job);
        if(changes==null||changes.size()<1||changes.size()>100)fail("STRUCTURE_LIMIT","Provide1..100 complete structural changes");
        IdentityHashMap<PlacementsHolder<?>,Membership> shared=new IdentityHashMap<>();IdentityHashMap<Node,Membership> instances=new IdentityHashMap<>();
        Set<String> touched=new TreeSet<>(),roots=new TreeSet<>();List<Map<String,Object>> previews=new ArrayList<>();
        for(JsonElement raw:changes){JsonObject row=object(raw,"structure change");String action=string(row,"action",16);
            if(!action.equals("add")&&!action.equals("remove"))fail("STRUCTURE_ACTION","Only add/remove are supported; rename is unavailable");
            if(action.equals("add"))fields(row,"action","scope","holder_instance_id","placement_id","placement");else fields(row,"action","scope","holder_instance_id","placement_id");
            String scope=string(row,"scope",64),holderId=string(row,"holder_instance_id",2048),ref=string(row,"placement_id",128);identifier(ref);
            if(!scope.equals("job_instance")&&!scope.equals("job_shared_definition"))fail("EDIT_SCOPE","Expected job_instance or job_shared_definition");
            Node anchor=graph.byId.get(holderId);if(anchor==null)fail("HOLDER_NOT_FOUND","No exact holder instance");
            if(anchor.source.getClass()!=BoardLocation.class)fail("STRUCTURE_BOARD_ONLY","Panel structural changes and pseudo records are unsupported");
            if(action.equals("add")&&!ref.matches("[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"))fail("INVALID_ID","New IDs must use bounded ASCII letters/digits/underscore/dot/hyphen");
            Value created=action.equals("add")?creation(config,object(row.get("placement"),"placement")):null;
            boolean all=scope.equals("job_shared_definition");List<Map<String,Object>> affected=new ArrayList<>();
            for(Node target:graph.nodes)if(target==anchor||(all&&target.source.getPlacementsHolder().getDefinition()==anchor.source.getPlacementsHolder().getDefinition())){
                String key=target.id+DELIMITER+ref;Placement prior=target.source.getPlacementsHolder().getPlacements().get(ref);
                if(!touched.add(key))fail("OVERLAPPING_EDITS","A logical record is targeted more than once");if(touched.size()>10000)fail("STRUCTURE_LIMIT","More than10000 expanded structural targets");
                if(action.equals("add")&&(prior!=null||state.reservedLogicalIds.contains(key)))fail("STRUCTURE_ID_REUSED","New logical ID already exists or is reserved/retired");
                if(action.equals("remove")&&prior==null)fail("PLACEMENT_NOT_FOUND","Removal requires an existing exact real placement");
                if(action.equals("remove"))for(Node ancestor:graph.nodes)if(ancestor.source.getClass()==PanelLocation.class)for(Placement pseudo:((PanelLocation)ancestor.source).getPanel().getPseudoPlacements())if((ancestor.id+DELIMITER+pseudo.getId()).equals(key))fail("STRUCTURE_PSEUDO_DEPENDENCY","Removal of a native pseudo-referenced real record is unsupported");
                roots.add(target.id.split(DELIMITER,2)[0]);affected.add(values("holder_instance_id",target.id,"logical_id",key,"before",prior==null?null:new Value(prior).dto(),"after",created==null?null:created.dto(),"holder_enabled",target.source.isEnabled(),"global_side",target.source.getGlobalSide()));
            }
            Membership edit=all?shared.computeIfAbsent(anchor.source.getPlacementsHolder().getDefinition(),k->new Membership()):instances.computeIfAbsent(anchor,k->new Membership());
            if(created==null)edit.removals.add(ref);else edit.additions.put(ref,created);
            previews.add(values("action",action,"scope",scope,"placement_id",ref,"affected",affected));
        }
        if(state.reservedLogicalIds.size()+touched.size()>100000)fail("STRUCTURE_LIMIT","Reservation inventory exceeds100000 logical IDs");
        StructurePatch patch=new StructurePatch(config,job,graph,authority,fence,state,shared,instances,touched,roots,previews);
        int expanded=0;for(Node node:graph.nodes){expanded+=patch.desired(node).size();if(node.source.getClass()==PanelLocation.class)expanded+=((PanelLocation)node.source).getPanel().getPseudoPlacements().size();if(expanded>MAX_EXPANDED_PLACEMENTS)fail("GRAPH_LIMIT","Result exceeds10000 expanded real/pseudo records");}
        return patch;
    }
    public static final class StructurePatch implements StagedStructure {
        final Configuration config;final Job job;final Graph graph;final StructureAuthority authority;final StructurePublicationFence fence;final StructureState state;
        final IdentityHashMap<PlacementsHolder<?>,Membership> shared;final IdentityHashMap<Node,Membership> instances;final Set<String> reserved;final List<String> roots;final String previewJson;final String transactionId=UUID.randomUUID().toString();boolean consumed;
        StructurePatch(Configuration c,Job j,Graph g,StructureAuthority a,StructurePublicationFence f,StructureState s,IdentityHashMap<PlacementsHolder<?>,Membership> shared,IdentityHashMap<Node,Membership> instances,Set<String> reserved,Set<String> roots,List<Map<String,Object>> changes)throws Exception{config=c;job=j;graph=g;authority=a;fence=f;state=s;this.shared=shared;this.instances=instances;this.reserved=Collections.unmodifiableSet(new TreeSet<>(reserved));this.roots=Collections.unmodifiableList(new ArrayList<>(roots));List<Object> inventory=new ArrayList<>();for(Node n:g.nodes)inventory.add(values("holder_instance_id",n.id,"placement_ids",new ArrayList<>(desired(n).keySet())));previewJson=GSON.toJson(values("valid",true,"scope","current-unexecuted-job-only","lineage_id",s.lineageId,"lineage_revision",s.revision,"source_fingerprint",g.fingerprint,"changes",changes,"result_inventory",inventory,"affected_root_ids",this.roots,"reserved_logical_ids",this.reserved,"source_files_modified",false,"material_history_modified",false,"registration_invalidated",true,"requires_explicit_load_rebinding",true,"hardware_qualified",false,"current_bridge_lineage_support",false));if(previewJson.getBytes(StandardCharsets.UTF_8).length>4*1024*1024)fail("STRUCTURE_PREVIEW_LIMIT","Split structure changes; preview exceeds4MiB");}
        Map<String,Value> desired(Node node){Map<String,Value> rows=membership(node.source.getPlacementsHolder());Membership s=shared.get(node.source.getPlacementsHolder().getDefinition());if(s!=null)s.apply(rows);Membership i=instances.get(node);if(i!=null)i.apply(rows);return rows;}
        public JsonObject preview(){return new JsonParser().parse(previewJson).getAsJsonObject();}
        private void current()throws Exception {if(!state.same(structureAuthority(config,job,authority)))fail("STRUCTURE_LINEAGE_STALE","Lineage authority changed after staging");Graph now=new Graph(config,job);if(!graph.sameIdentities(now)||!graph.fingerprint.equals(now.fingerprint))fail("STALE_PLACEMENT_PLAN","Native identity/graph/history changed after structure preview");for(Node node:graph.nodes)for(Value v:desired(node).values())validateValue(config,v);}
        private String withoutRegistration(Graph g){JsonArray rows=GSON.toJsonTree(g.snapshot).getAsJsonArray();for(JsonElement row:rows)if(row.isJsonObject())row.getAsJsonObject().remove("transform");return rows.toString();}
        public synchronized Map<String,Object> apply()throws Exception {
            if(consumed)fail("PLAN_CONSUMED","Structural plan already attempted");consumed=true;current();
            StructureBuilder builder=new StructureBuilder(this);IdentifiableList<PlacementsHolderLocation<?>> candidates=new IdentifiableList<>();
            for(Node node:graph.roots){Spec spec=builder.spec(node);PlacementsHolderLocation<?> copy=builder.instantiate(spec);copy.setParent(job.getRootPanelLocation());if(copy.getClass()==PanelLocation.class)PanelLocation.setParentsOfAllDescendants((PanelLocation)copy);candidates.add(copy);}builder.verify(candidates);current();
            JsonObject reservation=GSON.toJsonTree(values("kind","placement-structure-reservation-v1","transaction_id",transactionId,"lineage_id",state.lineageId,"expected_lineage_revision",state.revision,"source_fingerprint",graph.fingerprint,"logical_ids",reserved,"affected_root_ids",roots,"physical_effect",false)).getAsJsonObject();
            StructureState committedState;
            try{authority.reserveAndForce(state,reservation);StructureState next=structureAuthority(config,job,authority);Set<String> expected=new TreeSet<>(state.reservedLogicalIds);expected.addAll(reserved);if(!next.lineageId.equals(state.lineageId)||next.revision!=state.revision+1||!next.reservedLogicalIds.equals(expected))fail("STRUCTURE_AUTHORITY_INVALID","Reservation did not produce the exact durable next state");committedState=next;}
            catch(Throwable failure){throw new Fault("STRUCTURE_RESERVATION_UNCERTAIN","Reservation may be durable; do not replay or reuse logical IDs; no root publication was attempted",failure);}
            try{fence.invalidateBeforePublication(job,roots);if(!committedState.same(structureAuthority(config,job,authority)))fail("STRUCTURE_LINEAGE_STALE","Authority changed during invalidation");Graph afterFence=new Graph(config,job);if(!graph.sameIdentities(afterFence)||!withoutRegistration(graph).equals(withoutRegistration(afterFence)))fail("STALE_PLACEMENT_PLAN","Invalidation changed native membership or content");for(Node node:graph.nodes)for(Value v:desired(node).values())validateValue(config,v);}catch(Throwable failure){throw new Fault("STRUCTURE_FENCE_FAILED","Reservation retained; caller must fence invalidation failure before publication",failure);}
            Panel root=job.getRootPanelLocation().getPanel();IdentifiableList<PlacementsHolderLocation<?>> old=new IdentifiableList<>();old.addAll(root.getChildren());boolean oldJobDirty=job.isDirty(),oldRootDirty=root.isDirty();
            try{for(PlacementsHolderLocation<?> child:candidates)child.addPropertyChangeListener(root);root.setChildren(candidates);job.setDirty(true);}
            catch(Throwable error){Throwable rollback=null;try{root.setChildren(old);for(PlacementsHolderLocation<?> child:candidates)if(child.isListener(root))child.removePropertyChangeListener(root);root.setDirty(oldRootDirty);job.setDirty(oldJobDirty);}catch(Throwable failure){rollback=failure;}Fault fault=new Fault(rollback==null?"PLACEMENT_COMMIT_FAILED":"PLACEMENT_ROLLBACK_FAILED","Structure publication failed; reservation retained and caller must fence, even after rollback",error);if(rollback!=null)fault.addSuppressed(rollback);throw fault;}
            for(PlacementsHolderLocation<?> child:old)if(child.isListener(root))child.removePropertyChangeListener(root);retireOriginalInstances(graph);
            return values("applied",true,"lineage_id",state.lineageId,"lineage_revision",state.revision+1,"transaction_id",transactionId,"reserved_logical_ids",reserved,"job_identity_preserved",true,"source_files_modified",false,"material_history_modified",false,"registration_invalidated",true,"requires_explicit_load_rebinding",true,"hardware_qualified",false);
        }
    }
    private static final class StructureBuilder {
        final StructurePatch patch;final IdentityHashMap<PlacementsHolder<?>,PlacementsHolder<?>> base=new IdentityHashMap<>();int variants;
        StructureBuilder(StructurePatch p){patch=p;}
        void fill(PlacementsHolder<?> holder,Map<String,Value> rows){for(Map.Entry<String,Value> e:rows.entrySet()){Placement q=new Placement(e.getKey());e.getValue().set(q);q.setDefinition(q);holder.addPlacement(q);}}
        PlacementsHolder<?> definition(PlacementsHolder<?> original)throws Exception {PlacementsHolder<?> existing=base.get(original);if(existing!=null)return existing;PlacementsHolder<?> copy=empty(original);base.put(original,copy);Map<String,Value> rows=membership(original);Membership edit=patch.shared.get(original);if(edit!=null)edit.apply(rows);fill(copy,rows);
            if(original.getClass()==Panel.class){Panel src=(Panel)original,dst=(Panel)copy;for(PlacementsHolderLocation<?> child:src.getChildren()){PlacementsHolder<?> def=definition(child.getPlacementsHolder().getDefinition());PlacementsHolderLocation<?> q=location(child,def);restore(child,q,null);dst.addChild(q);}pseudo(src,dst);}return copy;}
        Spec spec(Node node)throws Exception {Spec spec=new Spec(node);PlacementsHolder<?> shared=definition(node.source.getPlacementsHolder().getDefinition());Map<String,Value> desired=patch.desired(node),defaults=membership(shared);boolean variant=!desired.keySet().equals(defaults.keySet());if(!variant)for(String id:desired.keySet())if(!desired.get(id).sameDefinition(defaults.get(id)))variant=true;
            for(int i=0;i<node.children.size();i++){Spec child=spec(node.children.get(i));spec.children.add(child);if(child.definition!=((Panel)shared).getChild(i).getPlacementsHolder().getDefinition())variant=true;}
            if(!variant){spec.definition=shared;return spec;}if(base.size()+ ++variants>MAX_DEFINITIONS)fail("GRAPH_LIMIT","Private definition expansion exceeds1000");PlacementsHolder<?> unique=empty(shared);fill(unique,desired);
            if(unique.getClass()==Panel.class){Panel panel=(Panel)unique;for(Spec child:spec.children){PlacementsHolderLocation<?> q=location(child.node.source,child.definition);restore(child.node.source,q,child);panel.addChild(q);}pseudo((Panel)shared,panel);}spec.definition=unique;return spec;}
        PlacementsHolderLocation<?> instantiate(Spec spec)throws Exception {PlacementsHolderLocation<?> q=location(spec.node.source,spec.definition);restore(spec.node.source,q,spec);return q;}
        void restore(PlacementsHolderLocation<?> source,PlacementsHolderLocation<?> copy,Spec spec)throws Exception {
            copy.setLocallyEnabled(source.isLocallyEnabled());copy.setCheckFiducials(source.isCheckFiducials());copy.setLocalToParentTransform(null);
            Map<String,Value> rows=spec==null?membership(source.getPlacementsHolder()):patch.desired(spec.node);if(spec==null){Membership shared=patch.shared.get(source.getPlacementsHolder().getDefinition());if(shared!=null)shared.apply(rows);}
            if(!rows.keySet().equals(membership(copy.getPlacementsHolder()).keySet()))fail("COPY_MISMATCH","Detached placement membership differs from staged result");for(Map.Entry<String,Value> e:rows.entrySet()){Placement q=copy.getPlacementsHolder().getPlacements().get(e.getKey());Value v=e.getValue();if(!new Value(q).sameDefinition(v))fail("COPY_MISMATCH","Detached placement content differs from staged result");q.setEnabled(v.enabled);q.setErrorHandling(v.error);}
            if(source.getClass()==PanelLocation.class)for(int i=0;i<((PanelLocation)source).getChildren().size();i++)restore(((PanelLocation)source).getChildren().get(i),((PanelLocation)copy).getChildren().get(i),spec==null?null:spec.children.get(i));
        }
        void verify(List<PlacementsHolderLocation<?>> roots)throws Exception {int[] count={0};for(int i=0;i<roots.size();i++)verifyNode(patch.graph.roots.get(i),roots.get(i),count);if(base.size()+variants>MAX_DEFINITIONS)fail("GRAPH_LIMIT","Private definition capacity exceeded");Set<PlacementsHolderLocation<?>> locations=identitySet();Set<PlacementsHolder<?>> holders=identitySet();ArrayDeque<PlacementsHolderLocation<?>> pending=new ArrayDeque<>(roots);int records=0;
            while(!pending.isEmpty()){PlacementsHolderLocation<?> l=pending.remove();if(!locations.add(l))continue;if(locations.size()>MAX_LOCATIONS)fail("GRAPH_LIMIT","Private locations exceed5000");for(PlacementsHolder<?> h:Arrays.asList(l.getPlacementsHolder(),l.getPlacementsHolder().getDefinition()))if(holders.add(h)){records+=h.getPlacements().size();if(h.getClass()==Panel.class){records+=((Panel)h).getPseudoPlacements().size();pending.addAll(((Panel)h).getChildren());}if(records>MAX_RECORDS)fail("GRAPH_LIMIT","Private records exceed100000");}}
        }
        void verifyNode(Node source,PlacementsHolderLocation<?> copy,int[] count)throws Exception {if(!source.id.equals(copy.getUniqueId())||copy==source.source||copy.getPlacementsHolder()==source.source.getPlacementsHolder()||copy.getPlacementsHolder().getDefinition()==source.source.getPlacementsHolder().getDefinition())fail("COPY_MISMATCH","Original native identities leaked into detached tree");if(copy.getPlacementsTransformStatus()!=PlacementsHolderLocation.PlacementsTransformStatus.NotSet)fail("COPY_MISMATCH","Registration leaked into detached result");Map<String,Value> expected=patch.desired(source),actual=membership(copy.getPlacementsHolder());if(!expected.keySet().equals(actual.keySet()))fail("COPY_MISMATCH","Final membership mismatch");for(String id:expected.keySet())if(!expected.get(id).same(actual.get(id)))fail("COPY_MISMATCH","Final record content mismatch");count[0]+=actual.size();
            if(copy.getClass()==PanelLocation.class){Panel a=((PanelLocation)source.source).getPanel(),b=((PanelLocation)copy).getPanel();if(a.getPseudoPlacements().size()!=b.getPseudoPlacements().size())fail("COPY_MISMATCH","Pseudo inventory changed");for(int i=0;i<a.getPseudoPlacements().size();i++)if(!Objects.equals(a.getPseudoPlacement(i).getId(),b.getPseudoPlacement(i).getId())||!new Value(a.getPseudoPlacement(i)).same(new Value(b.getPseudoPlacement(i))))fail("COPY_MISMATCH","Unstaged pseudo changed");count[0]+=b.getPseudoPlacements().size();for(int i=0;i<source.children.size();i++)verifyNode(source.children.get(i),((PanelLocation)copy).getChildren().get(i),count);}
            if(count[0]>MAX_EXPANDED_PLACEMENTS)fail("GRAPH_LIMIT","Expanded result exceeds10000 records");}
    }

    /** Bounded job-local board-child membership. No public Bridge admission is implied. */
    public static PanelStructurePatch stagePanelStructure(Configuration config,Job job,JsonObject change,
            StructureAuthority authority,PanelPublicationFence fence)throws Exception {
        if(fence==null)fail("STRUCTURE_FENCE_REQUIRED","A synchronous panel publication authority is required");
        StructureState state=structureAuthority(config,job,authority);Graph graph=new Graph(config,job);
        if(change==null)fail("INVALID_EDIT","One explicit panel change is required");
        String action=string(change,"action",32);
        boolean clone=action.equals("clone_board_child");
        if(!clone&&!action.equals("remove_board_child"))fail("STRUCTURE_ACTION","Expected clone_board_child or remove_board_child");
        if(clone)fields(change,"action","scope","parent_instance_id","source_child_id","new_child_id","location","side","enabled","check_fiducials");
        else fields(change,"action","scope","parent_instance_id","child_id");
        if(!string(change,"scope",32).equals("job_instance"))fail("EDIT_SCOPE","Panel membership supports one exact job_instance only");
        Node parent=graph.byId.get(string(change,"parent_instance_id",512));
        if(parent==null||parent.source.getClass()!=PanelLocation.class)fail("PANEL_NOT_FOUND","Select an existing non-inline-root panel instance");
        String sourceId=string(change,clone?"source_child_id":"child_id",128);identifier(sourceId);
        Node source=null;for(Node child:parent.children)if(child.source.getId().equals(sourceId))source=child;
        if(source==null)fail("HOLDER_NOT_FOUND","Select an exact direct child of the panel");
        if(source.source.getClass()!=BoardLocation.class)fail("STRUCTURE_BOARD_ONLY","Only direct exact BoardLocation children may be cloned or removed");
        String newId=clone?string(change,"new_child_id",128):null;
        if(clone&&!newId.matches("[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"))fail("INVALID_ID","New child ID requires bounded ASCII letters/digits/underscore/dot/hyphen");
        if(clone)for(Node child:parent.children)if(child.source.getId().equals(newId))fail("STRUCTURE_ID_REUSED","A child with the proposed ID already exists");
        if(!clone){int remaining=0;for(Node node:graph.nodes)if(node.source.getClass()==BoardLocation.class&&node.id.startsWith(parent.id+DELIMITER)&&node!=source)remaining++;if(remaining==0)fail("STRUCTURE_EMPTY_PANEL","Removal must leave a board descendant in the selected panel");}
        Location proposed=clone?parseLocation(change.get("location")):null;
        Side side=clone?enumValue(Side.class,string(change,"side",16)):null;
        boolean enabled=clone&&bool(change,"enabled"),fiducials=clone&&bool(change,"check_fiducials");
        for(Node node:graph.nodes)holderKey(node.id); // Complete current graph, including disabled/empty nodes.
        Map<String,String> pseudoTargets=exactPseudoTargets(graph);
        if(!clone)for(String target:pseudoTargets.values())if(target.startsWith(source.id+DELIMITER))fail("STRUCTURE_PSEUDO_DEPENDENCY","An ancestor pseudo record depends on the removed board");
        String touched=clone?parent.id+DELIMITER+newId:source.id;
        Set<String> reserved=new TreeSet<>();reserved.add(holderKey(touched));
        for(Placement placement:source.source.getPlacementsHolder().getPlacements())reserved.add(touched+DELIMITER+placement.getId());
        if(clone)for(String key:reserved)if(state.reservedLogicalIds.contains(key))fail("STRUCTURE_ID_REUSED","The new holder or one of its records is reserved/retired");
        Set<String> union=new TreeSet<>(state.reservedLogicalIds);union.addAll(reserved);if(union.size()>100000)fail("STRUCTURE_LIMIT","Reservation inventory exceeds100000 keys");
        return new PanelStructurePatch(config,job,graph,authority,fence,state,parent,source,clone,newId,proposed,side,enabled,fiducials,reserved,pseudoTargets);
    }
    /** UTF-8 must be injective over admitted native IDs: reject unpaired UTF-16 surrogates. */
    private static String holderKey(String path)throws Exception {
        if(path==null||path.isEmpty()||path.length()>512||path.chars().anyMatch(Character::isISOControl))fail("PANEL_HOLDER_ID","Native load holder paths must be1..512 characters without controls");
        try{
            java.nio.ByteBuffer encoded=StandardCharsets.UTF_8.newEncoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT).onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT).encode(java.nio.CharBuffer.wrap(path));
            byte[] bytes=new byte[encoded.remaining()];encoded.get(bytes);String key="holder-v1:"+Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
            if(key.length()>4096)fail("STRUCTURE_LIMIT","Encoded holder reservation exceeds4096 characters");return key;
        }catch(java.nio.charset.CharacterCodingException invalid){throw new Fault("PANEL_HOLDER_ID","Native holder paths require well-formed Unicode",invalid);}
    }
    private static Map<String,String> exactPseudoTargets(Graph graph)throws Exception {
        Map<String,String> targets=new LinkedHashMap<>();
        for(Node node:graph.nodes)if(node.source.getClass()==PanelLocation.class){
            Panel panel=((PanelLocation)node.source).getPanel();
            for(Placement pseudo:panel.getPseudoPlacements()){
                org.openpnp.util.Pair<List<PlacementsHolderLocation<?>>,Placement> pair=panel.getDescendantPlacement(pseudo.getId());
                if(pair==null||pair.first==null||pair.first.isEmpty()||pair.second==null)fail("STRUCTURE_PSEUDO_REFERENCE","Unresolved native pseudo reference");
                List<String> segments=new ArrayList<>();for(PlacementsHolderLocation<?> branch:pair.first)segments.add(branch.getId());segments.add(pair.second.getId());String relative=String.join(DELIMITER,segments);
                if(!relative.equals(pseudo.getId()))fail("STRUCTURE_PSEUDO_REFERENCE","Native prefix resolver changed the exact pseudo branch: "+pseudo.getId());
                String key=node.id+DELIMITER+pseudo.getId(),target=node.id+DELIMITER+relative;
                Node leaf=graph.byId.get(target.substring(0,target.lastIndexOf(DELIMITER)));
                if(leaf==null||leaf.source!=pair.first.get(pair.first.size()-1)||leaf.source.getPlacementsHolder().getPlacements().get(pair.second.getId())!=pair.second)fail("STRUCTURE_PSEUDO_REFERENCE","Pseudo target identity is outside the expanded graph");
                targets.put(key,target);
            }
        }
        return targets;
    }
    private static final class PanelSpec {
        final Node source;final String id,localId;final Location location;final Side side;final boolean enabled,fiducials;final List<PanelSpec> children=new ArrayList<>();PlacementsHolder<?> definition;
        PanelSpec(Node source,String id,String localId,Location location,Side side,boolean enabled,boolean fiducials){this.source=source;this.id=id;this.localId=localId;this.location=location;this.side=side;this.enabled=enabled;this.fiducials=fiducials;}
    }
    public static final class PanelStructurePatch implements StagedStructure {
        final Configuration config;final Job job;final Graph graph;final StructureAuthority authority;final PanelPublicationFence fence;final StructureState state;
        final PanelLocation originalRootLocation;final Panel originalRoot;final String originalRootName;final java.io.File originalRootFile;final Location originalRootDimensions;
        final Node parent,source;final boolean clone;final String newId,transactionId=UUID.randomUUID().toString();final Location proposed;final Side side;final boolean enabled,fiducials;
        final Set<String> reserved;final List<String> roots;final Map<String,String> pseudoTargets;final List<PanelSpec> specs=new ArrayList<>();final String previewJson;
        final IdentityHashMap<Part,org.openpnp.model.Package> parts=new IdentityHashMap<>();final IdentityHashMap<Object,String> library=new IdentityHashMap<>();
        boolean consumed;
        PanelStructurePatch(Configuration c,Job j,Graph g,StructureAuthority a,PanelPublicationFence f,StructureState s,Node p,Node from,boolean clone,String id,Location pose,Side side,boolean enabled,boolean fiducials,Set<String> reserved,Map<String,String> pseudos)throws Exception{
            config=c;job=j;graph=g;authority=a;fence=f;state=s;parent=p;source=from;this.clone=clone;newId=id;proposed=pose;this.side=side;this.enabled=enabled;this.fiducials=fiducials;this.reserved=Collections.unmodifiableSet(new TreeSet<>(reserved));pseudoTargets=Collections.unmodifiableMap(new LinkedHashMap<>(pseudos));roots=Collections.singletonList(parent.id.split(DELIMITER,2)[0]);
            originalRootLocation=j.getRootPanelLocation();originalRoot=originalRootLocation.getPanel();originalRootName=originalRoot.getName();originalRootFile=originalRoot.getFile();Location dimensions=originalRoot.getDimensions();originalRootDimensions=new Location(dimensions.getUnits(),dimensions.getX(),dimensions.getY(),dimensions.getZ(),dimensions.getRotation());
            List<Map<String,Object>> before=new ArrayList<>(),after=new ArrayList<>();for(Node n:g.roots)specs.add(spec(n,n.id,n.source.getId()));
            for(Node n:g.nodes)before.add(values("holder_instance_id",n.id,"kind",n.source.getClass()==BoardLocation.class?"board":"panel","placement_ids",new ArrayList<>(membership(n.source.getPlacementsHolder()).keySet())));
            int[] counts={0,0};for(PanelSpec root:specs)inventory(root,after,counts);
            for(Node n:g.nodes)for(Placement placement:n.source.getPlacementsHolder().getPlacements()){Part part=placement.getPart();if(part!=null&&!parts.containsKey(part)){parts.put(part,part.getPackage());library.put(part,libraryFingerprint(part));if(!library.containsKey(part.getPackage()))library.put(part.getPackage(),libraryFingerprint(part.getPackage()));}}
            previewJson=GSON.toJson(values("valid",true,"changed",true,"profile","panel-board-membership-v1","action",clone?"clone_board_child":"remove_board_child","scope","job_instance","parent_instance_id",parent.id,"source_child_id",source.source.getId(),"new_child_id",newId,"lineage_id",s.lineageId,"lineage_revision",s.revision,"source_fingerprint",g.fingerprint,"before_inventory",before,"result_inventory",after,"affected_root_ids",roots,"reserved_logical_ids",this.reserved,"holder_reservation_encoding","holder-v1:base64url-strict-utf8","registration_invalidated",true,"all_root_confirmation_invalidated",true,"source_files_modified",false,"material_history_modified",false,"requires_explicit_load_rebinding",true,"hardware_qualified",false,"current_bridge_lineage_support",false));
            if(previewJson.getBytes(StandardCharsets.UTF_8).length>4*1024*1024)fail("STRUCTURE_PREVIEW_LIMIT","Complete panel preview exceeds4MiB");
        }
        private PanelSpec spec(Node n,String id,String localId)throws Exception{
            PanelSpec s=new PanelSpec(n,id,localId,n.source.getLocation(),n.source.getSide(),n.source.isLocallyEnabled(),n.source.isCheckFiducials());
            for(Node child:n.children)if(clone||n!=parent||child!=source)s.children.add(spec(child,id+DELIMITER+child.source.getId(),child.source.getId()));
            if(clone&&n==parent)s.children.add(new PanelSpec(source,id+DELIMITER+newId,newId,proposed,side,enabled,fiducials));return s;
        }
        private void inventory(PanelSpec s,List<Map<String,Object>> rows,int[] counts)throws Exception{
            holderKey(s.id);if(++counts[0]>1000)fail("PANEL_GRAPH_LIMIT","Native load mapping permits at most1000 expanded board/panel holders");
            List<String> ids=new ArrayList<>(membership(s.source.source.getPlacementsHolder()).keySet());counts[1]+=ids.size();if(s.source.source.getClass()==PanelLocation.class)counts[1]+=((PanelLocation)s.source.source).getPanel().getPseudoPlacements().size();if(counts[1]>MAX_EXPANDED_PLACEMENTS)fail("GRAPH_LIMIT","Panel result exceeds10000 expanded real/pseudo records");
            rows.add(values("holder_instance_id",s.id,"source_holder_instance_id",s.source.id,"kind",s.source.source.getClass()==BoardLocation.class?"board":"panel","location",pose(s.location),"side",s.side,"enabled",s.enabled,"check_fiducials",s.fiducials,"placement_ids",ids));for(PanelSpec child:s.children)inventory(child,rows,counts);
        }
        public JsonObject preview(){return new JsonParser().parse(previewJson).getAsJsonObject();}
        private void current(StructureState expected,boolean afterFence)throws Exception{
            currentRoot();
            if(!expected.same(structureAuthority(config,job,authority)))fail("STRUCTURE_LINEAGE_STALE","Exact panel authority changed after staging");Graph now=new Graph(config,job);
            if(!graph.sameIdentities(now)||!(afterFence?graphWithoutRegistration(graph).equals(graphWithoutRegistration(now)):graph.fingerprint.equals(now.fingerprint)))fail("STALE_PLACEMENT_PLAN","Native graph/history changed after panel staging");
            currentLibrary();
            if(!pseudoTargets.equals(exactPseudoTargets(now)))fail("STRUCTURE_PSEUDO_REFERENCE","A staged pseudo target changed");
        }
        private void currentLibrary()throws Exception{
            for(Map.Entry<Part,org.openpnp.model.Package> entry:parts.entrySet())if(config.getPart(entry.getKey().getId())!=entry.getKey()||entry.getKey().getPackage()!=entry.getValue()||config.getPackage(entry.getValue().getId())!=entry.getValue())fail("PART_IDENTITY","Staged part/package identity changed");
            for(Map.Entry<Object,String> entry:library.entrySet())if(!entry.getValue().equals(libraryFingerprint(entry.getKey())))fail("PART_IDENTITY","Staged part/package definition changed");
        }
        private void currentRoot()throws Exception{
            // The detached candidate owns a different root; publication must retain the live root.
            if(job!=graph.identity.get(0)||job.getRootPanelLocation()!=originalRootLocation||originalRootLocation.getPanel()!=originalRoot)fail("COPY_MISMATCH","The original inline job root identity changed");
            if(!Objects.equals(originalRootName,originalRoot.getName())||!Objects.equals(originalRootFile,originalRoot.getFile())||originalRootDimensions.getUnits()!=originalRoot.getDimensions().getUnits()||!originalRootDimensions.equals(originalRoot.getDimensions()))fail("COPY_MISMATCH","The original inline job root metadata changed");
        }
        private void publicationCurrent(Graph built,StructureState expected)throws Exception{
            currentRoot();
            if(!expected.same(structureAuthority(config,job,authority)))fail("STRUCTURE_LINEAGE_STALE","Panel authority changed during native publication");
            Graph now=new Graph(config,job);
            // The detached Job and inline root differ; all published holder/record identities must match.
            if(built.identity.size()!=now.identity.size())fail("COPY_MISMATCH","Published panel identity count changed");
            for(int i=2;i<built.identity.size();i++)if(built.identity.get(i)!=now.identity.get(i))fail("COPY_MISMATCH","A native listener replaced a published panel identity");
            if(!GSON.toJson(graph.snapshot.get(0)).equals(GSON.toJson(now.snapshot.get(0)))||!GSON.toJson(built.snapshot.subList(1,built.snapshot.size())).equals(GSON.toJson(now.snapshot.subList(1,now.snapshot.size()))))fail("COPY_MISMATCH","A native listener changed the validated panel result");
            currentLibrary();if(!pseudoTargets.equals(exactPseudoTargets(now)))fail("STRUCTURE_PSEUDO_REFERENCE","Published panel pseudo targets changed");
        }
        public synchronized Map<String,Object> apply()throws Exception{
            if(consumed)fail("PLAN_CONSUMED","Panel structure plan already attempted");consumed=true;current(state,false);
            PanelBuilder builder=new PanelBuilder(this);Job candidate=builder.build();Graph built=new Graph(config,candidate);builder.verify(built);NativeBoardLoads.validateCandidate(candidate);NativeJobDocuments.validateCandidate(candidate);current(state,false);
            JsonObject reservation=GSON.toJsonTree(values("kind","panel-board-membership-reservation-v1","transaction_id",transactionId,"lineage_id",state.lineageId,"expected_lineage_revision",state.revision,"source_fingerprint",graph.fingerprint,"logical_ids",reserved,"affected_root_ids",roots,"physical_effect",false)).getAsJsonObject();StructureState committed;
            try{authority.reserveAndForce(state,reservation);committed=structureAuthority(config,job,authority);Set<String> expected=new TreeSet<>(state.reservedLogicalIds);expected.addAll(reserved);if(!committed.lineageId.equals(state.lineageId)||committed.revision!=state.revision+1||!committed.reservedLogicalIds.equals(expected))fail("STRUCTURE_AUTHORITY_INVALID","Reservation did not produce exact next state");}
            catch(Throwable failure){throw new Fault("STRUCTURE_RESERVATION_UNCERTAIN","Panel reservation may be durable; no publication was attempted; do not replay",failure);}
            try{fence.invalidateBeforePublication(job,roots);current(committed,true);}catch(Throwable failure){throw new Fault("STRUCTURE_FENCE_FAILED","Panel reservation retained; invalidation failed before publication",failure);}
            final Thread caller=Thread.currentThread();final boolean[] armed={true},published={false},attempted={false};final Throwable[] violation={null};final StructureState expected=committed;
            Publication publication=()->{
                if(!armed[0]||Thread.currentThread()!=caller||attempted[0]){Fault error=new Fault("STRUCTURE_PUBLICATION_PROTOCOL","Publication must run synchronously exactly once");violation[0]=error;throw error;}attempted[0]=true;
                try{current(expected,true);builder.publish(candidate);publicationCurrent(built,expected);published[0]=true;}catch(Throwable failure){violation[0]=failure;if(failure instanceof Error)throw (Error)failure;throw (Exception)failure;}
            };
            try{fence.publishIfCurrent(config,job,committed,publication);if(violation[0]!=null)throw new Fault("STRUCTURE_PUBLICATION_PROTOCOL","Publication callback suppressed a failure",violation[0]);if(!published[0])fail("STRUCTURE_PUBLICATION_PROTOCOL","Publication authority returned without invoking its setter");}
            catch(Throwable failure){throw new Fault("STRUCTURE_PUBLICATION_FAILED","Reservation retained; caller must fence uncertain panel publication",failure);}finally{armed[0]=false;}
            retireOriginalInstances(graph);
            return values("applied",true,"changed",true,"profile","panel-board-membership-v1","transaction_id",transactionId,"lineage_id",state.lineageId,"lineage_revision",committed.revision,"reserved_logical_ids",reserved,"affected_root_ids",roots,"result_inventory",preview().get("result_inventory"),"job_identity_preserved",true,"source_files_modified",false,"material_history_modified",false,"registration_invalidated",true,"all_root_confirmation_invalidated",true,"requires_explicit_load_rebinding",true,"hardware_qualified",false);
        }
    }
    private static String graphWithoutRegistration(Graph graph){JsonArray rows=GSON.toJsonTree(graph.snapshot).getAsJsonArray();for(JsonElement row:rows)if(row.isJsonObject())row.getAsJsonObject().remove("transform");return rows.toString();}
    private static String libraryFingerprint(Object object)throws Exception{
        final boolean[] overflow={false};java.io.ByteArrayOutputStream bytes=new java.io.ByteArrayOutputStream(){
            private void bound(int n){if(n<0||n>8*1024*1024-count){overflow[0]=true;throw new IllegalStateException("Native library definition exceeds8MiB");}}
            @Override public synchronized void write(int b){bound(1);super.write(b);}
            @Override public synchronized void write(byte[] b,int off,int len){bound(len);super.write(b,off,len);}
        };
        try{Configuration.createSerializer().write(object,bytes);}catch(Exception failure){if(overflow[0])throw new Fault("STRUCTURE_LIMIT","Native library definition exceeds8MiB",failure);throw failure;}return sha(bytes.toByteArray());
    }
    private static final class PanelBuilder {
        final PanelStructurePatch patch;final IdentityHashMap<PlacementsHolder<?>,PlacementsHolder<?>> base=new IdentityHashMap<>();int variants;
        PanelBuilder(PanelStructurePatch patch){this.patch=patch;}
        PlacementsHolder<?> definition(PlacementsHolder<?> source)throws Exception{
            PlacementsHolder<?> prior=base.get(source);if(prior!=null)return prior;if(base.size()+variants>=MAX_DEFINITIONS)fail("GRAPH_LIMIT","Panel private definitions exceed1000");PlacementsHolder<?> copy=empty(source);base.put(source,copy);
            for(Placement p:source.getPlacements())copy.addPlacement(ownCopy(p));
            if(source.getClass()==Panel.class){Panel panel=(Panel)copy;for(PlacementsHolderLocation<?> child:((Panel)source).getChildren()){PlacementsHolderLocation<?> q=location(child,definition(child.getPlacementsHolder().getDefinition()));restoreOriginal(child,q);panel.addChild(q);}pseudo((Panel)source,panel);}return copy;
        }
        void choose(PanelSpec spec)throws Exception{
            PlacementsHolder<?> shared=definition(spec.source.source.getPlacementsHolder().getDefinition());boolean variant=spec.children.size()!=spec.source.children.size();
            for(PanelSpec child:spec.children)choose(child);
            if(shared.getClass()==Panel.class)for(int i=0;i<spec.children.size();i++){PanelSpec child=spec.children.get(i);if(i>=((Panel)shared).getChildren().size()){variant=true;continue;}PlacementsHolderLocation<?> original=((Panel)shared).getChild(i);if(!original.getId().equals(child.localId)||original.getPlacementsHolder().getDefinition()!=child.definition||!original.getLocation().equals(child.location)||original.getSide()!=child.side)variant=true;}
            if(!variant){spec.definition=shared;return;}if(base.size()+ ++variants>MAX_DEFINITIONS)fail("GRAPH_LIMIT","Panel private variants exceed1000");PlacementsHolder<?> unique=empty(shared);for(Placement p:shared.getPlacements())unique.addPlacement(ownCopy(p));Panel panel=(Panel)unique;for(PanelSpec child:spec.children)panel.addChild(instantiate(child));pseudo((Panel)shared,panel);spec.definition=unique;
        }
        PlacementsHolderLocation<?> instantiate(PanelSpec spec)throws Exception{PlacementsHolderLocation<?> q=location(spec.source.source,spec.definition);restore(spec,q);return q;}
        void restore(PanelSpec spec,PlacementsHolderLocation<?> q)throws Exception{
            q.setId(spec.localId);q.setLocation(spec.location);q.setSide(spec.side);q.setLocallyEnabled(spec.enabled);q.setCheckFiducials(spec.fiducials);q.setLocalToParentTransform(null);restoreRecords(spec.source.source,q);
            if(q.getClass()==PanelLocation.class){List<PlacementsHolderLocation<?>> children=((PanelLocation)q).getChildren();if(children.size()!=spec.children.size())fail("COPY_MISMATCH","Panel child membership changed during native copy");for(int i=0;i<children.size();i++)restore(spec.children.get(i),children.get(i));PanelLocation.setParentsOfAllDescendants((PanelLocation)q);}
        }
        void restoreRecords(PlacementsHolderLocation<?> source,PlacementsHolderLocation<?> copy)throws Exception{if(!membership(source.getPlacementsHolder()).keySet().equals(membership(copy.getPlacementsHolder()).keySet()))fail("COPY_MISMATCH","Panel clone placement membership changed");for(Placement p:source.getPlacementsHolder().getPlacements()){Placement q=copy.getPlacementsHolder().getPlacements().get(p.getId());Value value=new Value(p);if(!value.sameDefinition(new Value(q)))fail("COPY_MISMATCH","Panel clone native definition content changed");q.setEnabled(value.enabled);q.setErrorHandling(value.error);}}
        void restoreOriginal(PlacementsHolderLocation<?> source,PlacementsHolderLocation<?> copy)throws Exception{copy.setLocallyEnabled(source.isLocallyEnabled());copy.setCheckFiducials(source.isCheckFiducials());copy.setLocalToParentTransform(null);restoreRecords(source,copy);if(source.getClass()==PanelLocation.class)for(int i=0;i<((PanelLocation)source).getChildren().size();i++)restoreOriginal(((PanelLocation)source).getChildren().get(i),((PanelLocation)copy).getChildren().get(i));}
        Job build()throws Exception{Job candidate=new Job();candidate.setErrorHandling(patch.job.getErrorHandling());IdentifiableList<PlacementsHolderLocation<?>> children=new IdentifiableList<>();for(PanelSpec spec:patch.specs){choose(spec);PlacementsHolderLocation<?> q=instantiate(spec);q.setParent(candidate.getRootPanelLocation());children.add(q);}candidate.getRootPanelLocation().getPanel().setChildren(children);PanelLocation.setParentsOfAllDescendants(candidate.getRootPanelLocation());return candidate;}
        void verify(Graph candidate)throws Exception{
            if(!patch.pseudoTargets.equals(exactPseudoTargets(candidate)))fail("STRUCTURE_PSEUDO_REFERENCE","Candidate pseudo binding differs from the exact staged target");Set<String> seen=new HashSet<>();for(PanelSpec spec:patch.specs)verifyNode(spec,candidate,seen);if(seen.size()!=candidate.nodes.size())fail("COPY_MISMATCH","Unexpected panel output holder");
        }
        void verifyNode(PanelSpec spec,Graph graph,Set<String> seen)throws Exception{Node node=graph.byId.get(spec.id);if(node==null||!seen.add(spec.id))fail("COPY_MISMATCH","Missing/duplicate exact panel output ID");PlacementsHolderLocation<?> q=node.source,old=spec.source.source;if(q==old||q.getPlacementsHolder()==old.getPlacementsHolder()||q.getPlacementsHolder().getDefinition()==old.getPlacementsHolder().getDefinition()||q.getPlacementsHolder().getDefinition()!=spec.definition)fail("COPY_MISMATCH","Source native holder identity leaked into candidate");if(q.getPlacementsTransformStatus()!=PlacementsHolderLocation.PlacementsTransformStatus.NotSet||!q.getLocation().equals(spec.location)||q.getSide()!=spec.side||q.isLocallyEnabled()!=spec.enabled||q.isCheckFiducials()!=spec.fiducials)fail("COPY_MISMATCH","Panel output pose/flags/registration differ");for(Placement p:old.getPlacementsHolder().getPlacements()){Placement actual=q.getPlacementsHolder().getPlacements().get(p.getId());if(actual==p||!new Value(p).same(new Value(actual)))fail("COPY_MISMATCH","Panel output placement content differs");}if(q.getClass()==PanelLocation.class){Panel a=(Panel)old.getPlacementsHolder(),b=(Panel)q.getPlacementsHolder();if(a.getPseudoPlacements().size()!=b.getPseudoPlacements().size())fail("COPY_MISMATCH","Panel pseudo count changed");for(int i=0;i<a.getPseudoPlacements().size();i++)if(!a.getPseudoPlacement(i).getId().equals(b.getPseudoPlacement(i).getId())||!new Value(a.getPseudoPlacement(i)).same(new Value(b.getPseudoPlacement(i))))fail("COPY_MISMATCH","Panel pseudo content changed");}for(PanelSpec child:spec.children)verifyNode(child,graph,seen);}
        void publish(Job candidate)throws Exception{
            Panel root=patch.job.getRootPanelLocation().getPanel();IdentifiableList<PlacementsHolderLocation<?>> old=new IdentifiableList<>();old.addAll(root.getChildren());IdentifiableList<PlacementsHolderLocation<?>> next=new IdentifiableList<>();next.addAll(candidate.getRootPanelLocation().getChildren());boolean jobDirty=patch.job.isDirty(),rootDirty=root.isDirty();
            try{for(PlacementsHolderLocation<?> child:next){child.setParent(patch.job.getRootPanelLocation());if(child.getClass()==PanelLocation.class)PanelLocation.setParentsOfAllDescendants((PanelLocation)child);child.addPropertyChangeListener(root);}root.setChildren(next);patch.job.setDirty(true);}
            catch(Throwable error){Throwable rollback=null;try{root.setChildren(old);for(PlacementsHolderLocation<?> child:next)if(child.isListener(root))child.removePropertyChangeListener(root);root.setDirty(rootDirty);patch.job.setDirty(jobDirty);}catch(Throwable failure){rollback=failure;}Fault failed=new Fault(rollback==null?"PLACEMENT_COMMIT_FAILED":"PLACEMENT_ROLLBACK_FAILED","Panel root publication failed; retain reservation and fence",error);if(rollback!=null)failed.addSuppressed(rollback);throw failed;}
            for(PlacementsHolderLocation<?> child:old)if(child.isListener(root))child.removePropertyChangeListener(root);
        }
    }

    private static final class Edit {
        final JsonObject json;final Part part;
        Edit(Configuration c,JsonObject input)throws Exception {
            fields(input,"location","side","type","enabled","part_id","error_handling","comments","rank");
            if(input.entrySet().isEmpty())fail("EMPTY_EDIT","At least one supported placement field is required");
            JsonObject json=input;
            part=json.has("part_id")?c.getPart(string(json,"part_id",128)):null;
            if(json.has("part_id")&&(part==null||part.getPackage()==null||c.getPackage(part.getPackage().getId())!=part.getPackage()))fail("PART_NOT_FOUND","part_id must refer to a configured native Part with its configured Package");
            if(json.has("location"))parseLocation(json.get("location"));
            if(json.has("side"))enumValue(Side.class,string(json,"side",16));
            if(json.has("type")){String t=string(json,"type",16);if(!t.equals("Placement")&&!t.equals("Fiducial"))fail("INVALID_TYPE","Only Placement and Fiducial are supported");}
            if(json.has("enabled"))bool(json,"enabled");
            if(json.has("error_handling"))enumValue(Placement.ErrorHandling.class,string(json,"error_handling",16));
            if(json.has("comments"))string(json,"comments",2048,true);
            if(json.has("rank"))integer(json,"rank",-100000,100000);
            this.json=new JsonParser().parse(input.toString()).getAsJsonObject();
        }
        boolean geometry(){for(String key:Arrays.asList("location","side","type","part_id","comments","rank"))if(json.has(key))return true;return false;}
        void revalidatePart(Configuration c)throws Exception {if(part!=null&&(c.getPart(part.getId())!=part||part.getPackage()==null||c.getPackage(part.getPackage().getId())!=part.getPackage()))fail("STALE_PLACEMENT_PLAN","A newly selected part/package identity changed after preview");}
        Value apply(Value before)throws Exception {
            Value v=new Value(before);
            if(json.has("location"))v.location=parseLocation(json.get("location"));
            if(json.has("side"))v.side=enumValue(Side.class,string(json,"side",16));
            if(json.has("type"))v.type=enumValue(Placement.Type.class,string(json,"type",16));
            if(json.has("part_id"))v.part=part;
            if(json.has("enabled"))v.enabled=bool(json,"enabled");
            if(json.has("error_handling"))v.error=enumValue(Placement.ErrorHandling.class,string(json,"error_handling",16));
            if(json.has("comments"))v.comments=string(json,"comments",2048,true);
            if(json.has("rank"))v.rank=integer(json,"rank",-100000,100000);
            return v;
        }
    }

    private static final class Value {
        Location location;Side side;Placement.Type type;Part part;boolean enabled;Placement.ErrorHandling error;String comments;int rank;
        Value(){}
        Value(Placement p){location=p.getLocation();side=p.getSide();type=p.getType();part=p.getPart();enabled=p.isEnabled();error=p.getErrorHandling();comments=p.getComments();rank=p.getRank();}
        Value(Value v){location=v.location;side=v.side;type=v.type;part=v.part;enabled=v.enabled;error=v.error;comments=v.comments;rank=v.rank;}
        boolean same(Value v){return location.convertToUnits(LengthUnit.Millimeters).equals(v.location.convertToUnits(LengthUnit.Millimeters))&&side==v.side&&type==v.type&&part==v.part&&enabled==v.enabled&&error==v.error&&Objects.equals(comments,v.comments)&&rank==v.rank;}
        boolean sameDefinition(Value v){Value a=new Value(this),b=new Value(v);a.enabled=b.enabled;a.error=b.error;return a.same(b);}
        Map<String,Object> dto(){return values("location",pose(location),"side",side,"type",type,"part_id",part==null?null:part.getId(),"enabled",enabled,"error_handling",error,"comments",comments,"rank",rank);}
        void set(Placement p){if(!p.getLocation().equals(location))p.setLocation(location);if(p.getSide()!=side)p.setSide(side);if(p.getType()!=type)p.setType(type);if(p.getPart()!=part)p.setPart(part);if(p.isEnabled()!=enabled)p.setEnabled(enabled);if(p.getErrorHandling()!=error)p.setErrorHandling(error);if(!Objects.equals(p.getComments(),comments))p.setComments(comments);if(p.getRank()!=rank)p.setRank(rank);}
    }
    private static final class Node {final PlacementsHolderLocation<?> source;final String id;final List<Node> children=new ArrayList<>();Node(PlacementsHolderLocation<?> s,String id){source=s;this.id=id;}}

    /** Validates typed graph shape without native transforms (some native getters cache transforms). */
    private static final class Graph {
        final Configuration config;final Job job;final List<Node> nodes=new ArrayList<>(),roots=new ArrayList<>();
        final Map<String,Node> byId=new LinkedHashMap<>();final List<PlacementsHolder<?>> definitions=new ArrayList<>();
        final Set<PlacementsHolder<?>> seenDefinitions=identitySet();final Set<PlacementsHolderLocation<?>> seenLocations=identitySet();
        final Set<PlacementsHolder<?>> expandedHolders=identitySet();final Set<Placement> expandedInstances=identitySet();
        final boolean readOnlyPads;final Set<BoardPad> seenPads=identitySet();final Map<PlacementsHolder<?>,List<Object>> padRows=new IdentityHashMap<>();int padCount;
        final List<Object> identity=new ArrayList<>();final List<Object> snapshot=new ArrayList<>();int records,expanded;final String fingerprint;
        Graph(Configuration c,Job j)throws Exception {
            this(c,j,false);
        }
        Graph(Configuration c,Job j,boolean readOnlyPads)throws Exception {
            this.readOnlyPads=readOnlyPads;
            config=c;job=j;if(j.getClass()!=Job.class||j.getRootPanelLocation().getClass()!=PanelLocation.class)fail("UNSUPPORTED_JOB","Exact native Job and PanelLocation are required");
            Panel root=j.getRootPanelLocation().getPanel();
            if(root==null||root.getClass()!=Panel.class||root.getDefinition()!=root||!root.getPlacements().isEmpty()||!root.getPseudoPlacements().isEmpty()||!root.getPseudoPlacementIds().isEmpty())fail("UNSUPPORTED_JOB_ROOT","Inline root placements/pseudo placements or shared root panels are unsupported");
            finite(j.getRootPanelLocation().getLocation());
            if(root.getProfile()==root.getProfile()||j.getRootPanelLocation().getSide()!=Side.Top||!j.getRootPanelLocation().isLocallyEnabled()||j.getRootPanelLocation().isCheckFiducials()||!j.getRootPanelLocation().getLocation().convertToUnits(LengthUnit.Millimeters).equals(new Location(LengthUnit.Millimeters)))fail("UNSUPPORTED_JOB_ROOT","Custom root outlines, transforms or override flags do not round-trip as native job load instances");
            if(j.getRootPanelLocation().getPlacementsTransformStatus()!=PlacementsHolderLocation.PlacementsTransformStatus.NotSet)fail("UNSUPPORTED_JOB_ROOT","The inline root may not hold a measured registration transform");
            identity.add(j);identity.add(root);snapshot.add(values("file",j.getFile()==null?null:j.getFile().toString(),"root_location",pose(j.getRootPanelLocation().getLocation()),"root_side",j.getRootPanelLocation().getSide(),"root_enabled",j.getRootPanelLocation().isLocallyEnabled(),"error_handling",j.getErrorHandling()));
            Set<String> ids=new HashSet<>();for(PlacementsHolderLocation<?> child:root.getChildren()){identifier(child.getId());if(!ids.add(child.getId()))fail("DUPLICATE_HOLDER_ID","Duplicate sibling holder ID");roots.add(visit(child,j.getRootPanelLocation(),child.getId(),identitySet(),0));}
            fingerprint=sha(GSON.toJson(snapshot).getBytes(StandardCharsets.UTF_8));
        }
        Node visit(PlacementsHolderLocation<?> l,PanelLocation parent,String id,Set<PlacementsHolder<?>> ancestry,int depth)throws Exception {
            if(depth>MAX_DEPTH||nodes.size()>=MAX_LOCATIONS)fail("GRAPH_LIMIT","Native edit graph exceeds location/depth bounds");
            if(l.getClass()!=BoardLocation.class&&l.getClass()!=PanelLocation.class)fail("UNSUPPORTED_NATIVE_CLASS","Custom holder locations are unsupported");
            if(l.getParent()!=parent||!Objects.equals(id,l.getUniqueId())||!seenLocations.add(l)||byId.containsKey(id))fail("AMBIGUOUS_HOLDER_ID","Native location identity, parent or unique ID is ambiguous");
            PlacementsHolder<?> holder=l.getPlacementsHolder();checkHolder(holder);PlacementsHolder<?> definition=holder.getDefinition();
            if(!expandedHolders.add(holder))fail("ALIASED_JOB_INSTANCE","Each expanded load must own a distinct native holder instance; share definitions instead");
            for(Placement p:holder.getPlacements())if(!expandedInstances.add(p))fail("ALIASED_JOB_INSTANCE","Expanded native placement instances are aliased across loads");
            if(holder.getClass()==Panel.class)for(Placement p:((Panel)holder).getPseudoPlacements())if(!expandedInstances.add(p))fail("ALIASED_JOB_INSTANCE","Expanded native pseudo instances are aliased across loads");
            if(definition==null||definition.getClass()!=holder.getClass()||definition.getDefinition()!=definition||ancestry.contains(definition))fail("DEFINITION_CYCLE","Invalid or cyclic native holder definition");
            validateInstance(holder,definition);checkDefinition(definition,identitySet(),0);finite(l.getLocation());
            Node node=new Node(l,id);nodes.add(node);byId.put(id,node);identity.add(l);identity.add(holder);identity.add(definition);
            List<Object> placements=new ArrayList<>();for(Placement p:holder.getPlacements()){if(++expanded>MAX_EXPANDED_PLACEMENTS)fail("GRAPH_LIMIT","More than10000 expanded placements");identity.add(p);placements.add(values("id",p.getId(),"value",new Value(p).dto(),"placed",job.retrievePlacedStatus(l,p.getId())));}
            List<Object> pseudoRecords=new ArrayList<>();
            if(holder.getClass()==Panel.class){expanded+=((Panel)holder).getPseudoPlacements().size();if(expanded>MAX_EXPANDED_PLACEMENTS)fail("GRAPH_LIMIT","More than10000 expanded placements and pseudo records");for(Placement p:((Panel)holder).getPseudoPlacements()){identity.add(p);pseudoRecords.add(values("id",p.getId(),"value",new Value(p).dto(),"placed",job.retrievePlacedStatus(l,p.getId())));}}
            // Unset getters synthesize/cache a default transform in this pinned native model.
            // Read only explicitly registered matrices; inherited registration is captured at its owner.
            double[] transform=null;if(l.getPlacementsTransformStatus()==PlacementsHolderLocation.PlacementsTransformStatus.LocallySet){AffineTransform tx=l.getLocalToParentTransform();transform=new double[6];tx.getMatrix(transform);for(double x:transform)if(!Double.isFinite(x))fail("INVALID_GEOMETRY","Nonfinite registration transform");}
            Map<String,Object> nodeValues=values("id",id,"location",pose(l.getLocation()),"side",l.getSide(),"enabled",l.isLocallyEnabled(),"check_fiducials",l.isCheckFiducials(),"transform",transform,"placements",placements,"pseudo_records",pseudoRecords);
            if(readOnlyPads&&padRows.containsKey(holder)&&!padRows.get(holder).isEmpty())nodeValues.put("solder_paste_pads",padRows.get(holder));snapshot.add(nodeValues);
            if(l.getClass()==PanelLocation.class){Set<PlacementsHolder<?>> next=identitySet();next.addAll(ancestry);next.add(definition);Set<String> ids=new HashSet<>();for(PlacementsHolderLocation<?> child:((PanelLocation)l).getChildren()){identifier(child.getId());if(!ids.add(child.getId()))fail("DUPLICATE_HOLDER_ID","Duplicate sibling holder ID");node.children.add(visit(child,(PanelLocation)l,id+DELIMITER+child.getId(),next,depth+1));}}
            return node;
        }
        void checkDefinition(PlacementsHolder<?> d,Set<PlacementsHolder<?>> ancestry,int depth)throws Exception {
            if(depth>MAX_DEPTH||ancestry.contains(d))fail("DEFINITION_CYCLE","Cyclic or too-deep definition graph");
            if(!seenDefinitions.add(d))return;if(definitions.size()>=MAX_DEFINITIONS)fail("GRAPH_LIMIT","More than1000 native definitions");definitions.add(d);identity.add(d);checkHolder(d);
            List<Object> pvalues=new ArrayList<>();for(Placement p:d.getPlacements()){identity.add(p);pvalues.add(values("id",p.getId(),"value",new Value(p).dto()));}
            List<Object> children=new ArrayList<>();List<Object> pseudos=new ArrayList<>();
            if(d.getClass()==Panel.class){Set<PlacementsHolder<?>> next=identitySet();next.addAll(ancestry);next.add(d);Set<String> ids=new HashSet<>();for(PlacementsHolderLocation<?> child:((Panel)d).getChildren()){identifier(child.getId());if(!ids.add(child.getId()))fail("DUPLICATE_HOLDER_ID","Duplicate definition child ID");checkHolder(child.getPlacementsHolder());validateInstance(child.getPlacementsHolder(),child.getPlacementsHolder().getDefinition());finite(child.getLocation());children.add(values("id",child.getId(),"pose",pose(child.getLocation()),"side",child.getSide(),"enabled",child.isLocallyEnabled(),"check_fiducials",child.isCheckFiducials()));checkDefinition(child.getPlacementsHolder().getDefinition(),next,depth+1);}
                Set<String> pseudoIds=new HashSet<>();for(Placement p:((Panel)d).getPseudoPlacements()){if(p.getClass()!=PseudoPlacement.class||!pseudoIds.add(p.getId()))fail("UNSUPPORTED_PSEUDO","Definitions require unique native PseudoPlacement records");finite(p.getLocation());if(((Panel)d).getDescendantPlacement(p.getId())==null)fail("UNSUPPORTED_PSEUDO","Unresolvable pseudo placement");pseudos.add(values("id",p.getId(),"value",new Value(p).dto()));}
                if(!((Panel)d).getPseudoPlacementIds().isEmpty()&&!new HashSet<>(((Panel)d).getPseudoPlacementIds()).equals(pseudoIds))fail("UNSUPPORTED_PSEUDO","Persisted pseudo IDs differ from resolved records");}
            Map<String,Object> definitionValues=values("definition",definitions.indexOf(d),"name",d.getName(),"dimensions",pose(d.getDimensions()),"placements",pvalues,"children",children,"pseudos",pseudos);
            if(readOnlyPads&&padRows.containsKey(d)&&!padRows.get(d).isEmpty())definitionValues.put("solder_paste_pads",padRows.get(d));snapshot.add(definitionValues);
        }
        void checkHolder(PlacementsHolder<?> holder)throws Exception {
            if(holder==null||(holder.getClass()!=Board.class&&holder.getClass()!=Panel.class))fail("UNSUPPORTED_NATIVE_CLASS","Only exact native Board and Panel are supported");
            finite(holder.getDimensions());if(holder.getProfile()==holder.getProfile())fail("UNSUPPORTED_OUTLINE","Explicit custom outlines cannot be preserved by this pinned native copy path");
            if(holder.getClass()==Board.class&&!((Board)holder).getSolderPastePads().isEmpty()){
                if(!readOnlyPads)fail("UNSUPPORTED_PASTE_PADS","Solder paste pad editing/copy is outside this bounded placement adapter");
                capturePads((Board)holder);
            }
            Set<String> refs=new HashSet<>();for(Placement p:holder.getPlacements()){if(++records>MAX_RECORDS)fail("GRAPH_LIMIT","Native record traversal exceeds100000");if(p.getClass()!=Placement.class)fail("UNSUPPORTED_NATIVE_CLASS","Custom placement subclasses are unsupported");identifier(p.getId());if(!refs.add(p.getId()))fail("DUPLICATE_REFERENCE","Duplicate placement ID");validateValue(config,new Value(p));}
        }
        void capturePads(Board board)throws Exception {
            if(padRows.containsKey(board))return;List<Object> values=new ArrayList<>();padRows.put(board,values);
            for(BoardPad pad:board.getSolderPastePads()){
                if(++padCount>MAX_RECORDS)fail("GRAPH_LIMIT","Read-only solder paste pad inventory exceeds100000");
                if(pad==null||pad.getClass()!=BoardPad.class||!seenPads.add(pad))fail("UNSUPPORTED_PASTE_PADS","Read-only pads require distinct exact native BoardPad identities");
                if(pad.getSide()==null||pad.getType()==null||pad.getName()!=null&&(pad.getName().length()>2048||pad.getName().indexOf('\0')>=0))fail("UNSUPPORTED_PASTE_PADS","Invalid native solder paste pad metadata");
                finite(pad.getLocation());Pad shape=pad.getPad();
                if(shape==null||shape.getUnits()==null)fail("UNSUPPORTED_PASTE_PADS","A native solder paste pad shape with explicit units is required");
                Map<String,Object> dimensions;
                if(shape.getClass()==Pad.Circle.class){double radius=((Pad.Circle)shape).getRadius();padDimension(radius);dimensions=values("radius",radius);}
                else if(shape.getClass()==Pad.Ellipse.class){Pad.Ellipse p=(Pad.Ellipse)shape;padDimension(p.getWidth());padDimension(p.getHeight());dimensions=values("width",p.getWidth(),"height",p.getHeight());}
                else if(shape.getClass()==Pad.RoundRectangle.class){Pad.RoundRectangle p=(Pad.RoundRectangle)shape;padDimension(p.getWidth());padDimension(p.getHeight());padDimension(p.getRoundness());if(p.getRoundness()>1)fail("UNSUPPORTED_PASTE_PADS","Native pad roundness must be0..1");dimensions=values("width",p.getWidth(),"height",p.getHeight(),"roundness",p.getRoundness());}
                else{fail("UNSUPPORTED_PASTE_PADS","Read-only pads support exact native Circle, Ellipse and RoundRectangle shapes");return;}
                // Board's native copy constructor creates distinct BoardPads but deliberately
                // shares their Pad shape objects. Retain those identities without rejecting it.
                identity.add(pad);identity.add(shape);
                values.add(values("name",pad.getName(),"side",pad.getSide(),"type",pad.getType(),"location",pose(pad.getLocation()),"shape_class",shape.getClass().getName(),"shape_units",shape.getUnits().name(),"dimensions",dimensions));
            }
        }
        void padDimension(double value)throws Exception {if(!Double.isFinite(value)||value<0)fail("UNSUPPORTED_PASTE_PADS","Native pad dimensions must be finite and nonnegative");}
        void validateInstance(PlacementsHolder<?> h,PlacementsHolder<?> d)throws Exception {
            if(d==null||d.getClass()!=h.getClass())fail("DEFINITION_CONFLICT","Missing native definition");if(h==d)return;
            if(!Objects.equals(h.getName(),d.getName())||!h.getDimensions().equals(d.getDimensions())||h.getPlacements().size()!=d.getPlacements().size())fail("UNSUPPORTED_INSTANCE_DRIFT","Existing holder differs from its definition");
            for(Placement p:h.getPlacements()){Placement dp=d.getPlacements().get(p.getId());if(dp==null||p.getDefinition()!=dp||!new Value(p).sameDefinition(new Value(dp)))fail("UNSUPPORTED_INSTANCE_DRIFT","Existing instance-only geometry/content cannot be silently normalized");}
            if(h.getClass()==Panel.class){Panel a=(Panel)h,b=(Panel)d;if(a.getChildren().size()!=b.getChildren().size()||a.getPseudoPlacements().size()!=b.getPseudoPlacements().size())fail("UNSUPPORTED_INSTANCE_DRIFT","Panel child/pseudo inventory differs");for(int i=0;i<a.getChildren().size();i++){PlacementsHolderLocation<?> x=a.getChild(i),y=b.getChild(i);if(!Objects.equals(x.getId(),y.getId())||x.getClass()!=y.getClass()||!x.getLocation().equals(y.getLocation())||x.getSide()!=y.getSide()||x.getPlacementsHolder().getDefinition()!=y.getPlacementsHolder().getDefinition())fail("UNSUPPORTED_INSTANCE_DRIFT","Panel child geometry or definition differs");}for(int i=0;i<a.getPseudoPlacements().size();i++){Placement x=a.getPseudoPlacement(i),y=b.getPseudoPlacement(i);if(!Objects.equals(x.getId(),y.getId())||!new Value(x).same(new Value(y)))fail("UNSUPPORTED_INSTANCE_DRIFT","Panel pseudo instance differs");}}
        }
        boolean sameIdentities(Graph g){if(identity.size()!=g.identity.size())return false;for(int i=0;i<identity.size();i++)if(identity.get(i)!=g.identity.get(i))return false;return true;}
        List<String> rootIds(){List<String> result=new ArrayList<>();for(Node root:roots)result.add(root.id);return result;}
    }

    private static final class Spec {final Node node;PlacementsHolder<?> definition;final List<Spec> children=new ArrayList<>();Spec(Node n){node=n;}}
    private static final class Builder {
        final Patch patch;final IdentityHashMap<PlacementsHolder<?>,PlacementsHolder<?>> base=new IdentityHashMap<>();int variants;
        Builder(Patch p){patch=p;}
        PlacementsHolder<?> definition(PlacementsHolder<?> original)throws Exception {
            PlacementsHolder<?> existing=base.get(original);if(existing!=null)return existing;
            PlacementsHolder<?> copy=empty(original);base.put(original,copy);
            for(Placement p:original.getPlacements()){Placement q=ownCopy(p);Edit edit=patch.shared.get(p);if(edit!=null)edit.apply(new Value(q)).set(q);copy.addPlacement(q);}
            if(original.getClass()==Panel.class){Panel src=(Panel)original,dst=(Panel)copy;for(PlacementsHolderLocation<?> child:src.getChildren()){PlacementsHolder<?> def=definition(child.getPlacementsHolder().getDefinition());PlacementsHolderLocation<?> q=location(child,def);restoreOverrides(child,q,null);dst.addChild(q);}pseudo(src,dst);}
            return copy;
        }
        Spec spec(Node node)throws Exception {
            Spec spec=new Spec(node);PlacementsHolder<?> old=node.source.getPlacementsHolder();PlacementsHolder<?> shared=definition(old.getDefinition());boolean variant=false;
            for(Placement p:old.getPlacements()){Edit e=patch.instances.get(p);if(e!=null&&e.geometry()&&!new Value(p).same(e.apply(new Value(p))))variant=true;}
            for(int i=0;i<node.children.size();i++){Spec child=spec(node.children.get(i));spec.children.add(child);if(child.definition!=((Panel)shared).getChild(i).getPlacementsHolder().getDefinition())variant=true;}
            if(!variant){spec.definition=shared;return spec;}
            if(base.size()+ ++variants>MAX_DEFINITIONS)fail("GRAPH_LIMIT","Private definition expansion exceeds1000");
            PlacementsHolder<?> unique=empty(shared);
            for(Placement p:shared.getPlacements()){Placement q=ownCopy(p);Placement source=old.getPlacements().get(p.getId());Edit e=patch.instances.get(source);if(e!=null&&e.geometry())e.apply(new Value(q)).set(q);unique.addPlacement(q);}
            if(unique.getClass()==Panel.class){Panel panel=(Panel)unique;for(Spec child:spec.children){PlacementsHolderLocation<?> q=location(child.node.source,child.definition);restoreOverrides(child.node.source,q,child);panel.addChild(q);}pseudo((Panel)shared,panel);}
            spec.definition=unique;return spec;
        }
        PlacementsHolderLocation<?> instantiate(Spec spec)throws Exception {PlacementsHolderLocation<?> q=location(spec.node.source,spec.definition);restoreOverrides(spec.node.source,q,spec);return q;}
        void restoreOverrides(PlacementsHolderLocation<?> source,PlacementsHolderLocation<?> copy,Spec spec)throws Exception {
            copy.setLocallyEnabled(source.isLocallyEnabled());copy.setCheckFiducials(source.isCheckFiducials());copy.setLocalToParentTransform(null);
            for(Placement p:source.getPlacementsHolder().getPlacements()){
                Placement q=copy.getPlacementsHolder().getPlacements().get(p.getId());Value desired=new Value(p);Edit e=patch.shared.get(p.getDefinition());if(e!=null)desired=e.apply(desired);if(spec!=null){e=patch.instances.get(p);if(e!=null)desired=e.apply(desired);}
                // Geometry comes from the chosen private definition; only native job overrides live here.
                if(!new Value(q).sameDefinition(desired))fail("COPY_MISMATCH","Private native definition differs from the staged instance edit");q.setEnabled(desired.enabled);q.setErrorHandling(desired.error);
            }
            if(source.getClass()==PanelLocation.class)for(int i=0;i<((PanelLocation)source).getChildren().size();i++)restoreOverrides(((PanelLocation)source).getChildren().get(i),((PanelLocation)copy).getChildren().get(i),spec==null?null:spec.children.get(i));
        }
        void verify(List<PlacementsHolderLocation<?>> roots)throws Exception {
            int[] count={0};for(int i=0;i<roots.size();i++)verifyNode(patch.graph.roots.get(i),roots.get(i),count);
            if(base.size()+variants>MAX_DEFINITIONS)fail("GRAPH_LIMIT","Private definition expansion exceeds1000");
            Set<PlacementsHolderLocation<?>> locations=identitySet();Set<PlacementsHolder<?>> holders=identitySet();
            ArrayDeque<PlacementsHolderLocation<?>> pending=new ArrayDeque<>(roots);int records=0;
            while(!pending.isEmpty()){PlacementsHolderLocation<?> location=pending.remove();if(!locations.add(location))continue;if(locations.size()>MAX_LOCATIONS)fail("GRAPH_LIMIT","Private definition/instance location expansion exceeds5000");
                for(PlacementsHolder<?> holder:Arrays.asList(location.getPlacementsHolder(),location.getPlacementsHolder().getDefinition()))if(holders.add(holder)){records+=holder.getPlacements().size();if(holder.getClass()==Panel.class){records+=((Panel)holder).getPseudoPlacements().size();pending.addAll(((Panel)holder).getChildren());}if(records>MAX_RECORDS)fail("GRAPH_LIMIT","Private definition/instance records exceed100000");}}
        }
        void verifyNode(Node source,PlacementsHolderLocation<?> copy,int[] count)throws Exception {
            if(!Objects.equals(source.id,copy.getUniqueId())||copy==source.source||copy.getPlacementsHolder()==source.source.getPlacementsHolder()||copy.getPlacementsHolder().getDefinition()==source.source.getPlacementsHolder().getDefinition())fail("COPY_MISMATCH","Original native identity leaked into the candidate tree");
            if(copy.getPlacementsTransformStatus()!=PlacementsHolderLocation.PlacementsTransformStatus.NotSet)fail("COPY_MISMATCH","A stale registration transform leaked into the edited job");
            for(Placement p:source.source.getPlacementsHolder().getPlacements()){if(++count[0]>MAX_EXPANDED_PLACEMENTS)fail("GRAPH_LIMIT","Expanded copied placement bound exceeded");Value desired=new Value(p);Edit e=patch.shared.get(p.getDefinition());if(e!=null)desired=e.apply(desired);e=patch.instances.get(p);if(e!=null)desired=e.apply(desired);Placement q=copy.getPlacementsHolder().getPlacements().get(p.getId());if(q==null||q==p||!desired.same(new Value(q)))fail("COPY_MISMATCH","Native copy changed an unstaged placement field");}
            if(copy.getClass()==PanelLocation.class)for(int i=0;i<source.children.size();i++)verifyNode(source.children.get(i),((PanelLocation)copy).getChildren().get(i),count);
        }
    }

    private static PlacementsHolder<?> empty(PlacementsHolder<?> source){PlacementsHolder<?> copy=source.getClass()==Board.class?new Board():new Panel();copy.setName(source.getName());copy.setDimensions(source.getDimensions());copy.setFile(null);return copy;}
    /** Remove only known subscription edges owned by replaced job instances, never shared definitions. */
    private static void retireOriginalInstances(Graph graph){
        for(Node node:graph.nodes){PlacementsHolderLocation<?> location=node.source;PlacementsHolder<?> holder=location.getPlacementsHolder();
            if(location.getDefinition()!=location&&location.getDefinition().isListener(location))location.getDefinition().removePropertyChangeListener(location);
            if(location.isListener(graph.job))location.removePropertyChangeListener(graph.job);
            if(holder.getDefinition()!=holder&&holder.getDefinition().isListener(holder))holder.getDefinition().removePropertyChangeListener(holder);
            if(holder.isListener(graph.job))holder.removePropertyChangeListener(graph.job);
            for(Placement p:holder.getPlacements()){if(p.getDefinition()!=p&&p.getDefinition().isListener(p))p.getDefinition().removePropertyChangeListener(p);if(p.isListener(graph.job))p.removePropertyChangeListener(graph.job);}
            if(holder.getClass()==Panel.class)for(Placement p:((Panel)holder).getPseudoPlacements()){if(p.getDefinition()!=p&&p.getDefinition().isListener(p))p.getDefinition().removePropertyChangeListener(p);if(p.isListener(graph.job))p.removePropertyChangeListener(graph.job);}
        }
    }
    private static Placement ownCopy(Placement source){Placement copy=new Placement(source);copy.setDefinition(copy);return copy;}
    private static PlacementsHolderLocation<?> location(PlacementsHolderLocation<?> source,PlacementsHolder<?> definition){PlacementsHolderLocation<?> copy=source.getClass()==BoardLocation.class?new BoardLocation(new Board((Board)definition)):new PanelLocation(new Panel((Panel)definition));copy.setId(source.getId());copy.setLocation(source.getLocation());copy.setSide(source.getSide());copy.setLocallyEnabled(source.isLocallyEnabled());copy.setCheckFiducials(source.isCheckFiducials());copy.setFileName(null);if(copy.getClass()==PanelLocation.class)PanelLocation.setParentsOfAllDescendants((PanelLocation)copy);return copy;}
    private static void pseudo(Panel source,Panel copy)throws Exception {for(Placement original:source.getPseudoPlacements()){Placement derived=copy.createPseudoPlacement(original.getId());derived.setEnabled(original.isEnabled());derived.setErrorHandling(original.getErrorHandling());derived.setComments(original.getComments());derived.setRank(original.getRank());copy.addPseudoPlacement(derived);}}
    private static void validateValue(Configuration c,Value v)throws Exception {finite(v.location);if(v.side==null||(v.type!=Placement.Type.Placement&&v.type!=Placement.Type.Fiducial)||v.error==null)fail("INVALID_PLACEMENT","Invalid native side/type/error policy");if(v.part!=null&&(c.getPart(v.part.getId())!=v.part||v.part.getPackage()==null||c.getPackage(v.part.getPackage().getId())!=v.part.getPackage()))fail("PART_IDENTITY","Placement part/package does not match configured native identities");if(v.comments!=null&&v.comments.length()>2048)fail("TEXT_LIMIT","Placement comments exceed2048characters");}
    private static Location parseLocation(JsonElement raw)throws Exception {JsonObject p=object(raw,"location");fields(p,"frame","units","x","y","z","rotation");if(!string(p,"frame",16).equals("holder"))fail("COORDINATE_FRAME","Only local holder coordinates are accepted; no machine-frame or side mirroring inference");String units=string(p,"units",8);LengthUnit u=units.equals("mm")?LengthUnit.Millimeters:units.equals("in")?LengthUnit.Inches:null;if(u==null)fail("UNITS","Units must be mm or in");Location pose=new Location(u,number(p,"x"),number(p,"y"),number(p,"z"),number(p,"rotation"));Location mm=pose.convertToUnits(LengthUnit.Millimeters);if(Math.abs(mm.getX())>10000||Math.abs(mm.getY())>10000||Math.abs(mm.getZ())>1000||Math.abs(pose.getRotation())>360)fail("GEOMETRY_RANGE","Edit geometry exceeds10000mm XY,1000mm Z or360degrees");return mm;}
    private static void finite(Location p)throws Exception{if(p==null||p.getUnits()==null||!Double.isFinite(p.getX())||!Double.isFinite(p.getY())||!Double.isFinite(p.getZ())||!Double.isFinite(p.getRotation()))fail("INVALID_GEOMETRY","Native location must have finite values and explicit units");}
    private static Map<String,Object> pose(Location p){return values("frame","holder","units",p.getUnits().name(),"x",p.getX(),"y",p.getY(),"z",p.getZ(),"rotation",p.getRotation());}
    private static JsonObject object(JsonElement e,String label)throws Exception {if(e==null||!e.isJsonObject())fail("INVALID_EDIT",label+" must be an object");return e.getAsJsonObject();}
    private static void fields(JsonObject o,String... names)throws Exception {Set<String> allowed=new HashSet<>(Arrays.asList(names));for(Map.Entry<String,JsonElement> e:o.entrySet())if(!allowed.contains(e.getKey()))fail("UNKNOWN_FIELD","Unsupported field: "+e.getKey());}
    private static String string(JsonObject o,String k,int max)throws Exception{return string(o,k,max,false);}
    private static String string(JsonObject o,String k,int max,boolean empty)throws Exception {JsonElement e=o.get(k);if(e==null||!e.isJsonPrimitive()||!e.getAsJsonPrimitive().isString())fail("INVALID_FIELD",k+" must be a string");String s=e.getAsString();if((!empty&&s.isEmpty())||s.length()>max||s.indexOf('\0')>=0)fail("INVALID_FIELD",k+" is empty, oversized or contains NUL");return s;}
    private static boolean bool(JsonObject o,String k)throws Exception {JsonElement e=o.get(k);if(e==null||!e.isJsonPrimitive()||!e.getAsJsonPrimitive().isBoolean())fail("INVALID_FIELD",k+" must be boolean");return e.getAsBoolean();}
    private static double number(JsonObject o,String k)throws Exception {JsonElement e=o.get(k);if(e==null||!e.isJsonPrimitive()||!e.getAsJsonPrimitive().isNumber()||!Double.isFinite(e.getAsDouble()))fail("INVALID_FIELD",k+" must be a finite number");return e.getAsDouble();}
    private static int integer(JsonObject o,String k,int min,int max)throws Exception {double n=number(o,k);if(n!=Math.rint(n)||n<min||n>max)fail("INVALID_FIELD",k+" must be a bounded integer");return (int)n;}
    private static <E extends Enum<E>> E enumValue(Class<E> type,String s)throws Exception {try{return Enum.valueOf(type,s);}catch(IllegalArgumentException e){fail("INVALID_FIELD","Unsupported "+type.getSimpleName()+": "+s);return null;}}
    private static void identifier(String s)throws Exception {if(s==null||s.isEmpty()||s.length()>128||s.contains(DELIMITER)||s.indexOf('\0')>=0)fail("INVALID_ID","IDs must be nonempty,<=128characters and contain no native hierarchy delimiter/NUL");}
    private static <T> Set<T> identitySet(){return Collections.newSetFromMap(new IdentityHashMap<T,Boolean>());}
    private static String sha(byte[] bytes)throws Exception{StringBuilder out=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(bytes))out.append(String.format(Locale.ROOT,"%02x",b));return out.toString();}
    private static Map<String,Object> values(Object... pairs){Map<String,Object> out=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)out.put((String)pairs[i],pairs[i+1]);return out;}
    private static void fail(String code,String message)throws Fault{throw new Fault(code,message);}
}
