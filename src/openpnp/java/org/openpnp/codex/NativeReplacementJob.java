/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.awt.geom.*;
import java.beans.PropertyChangeListener;
import java.io.IOException;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.spi.Machine;
import org.openpnp.util.Pair;
import org.openpnp.util.IdentifiableList;

/** Builds an unpublished native replacement from the actual current Job. No XML, canonical DTO,
 * old Job initialization, placed-state setter, journal write, machine motion or library insertion.
 * Shared native definitions are copied once and all new instance/definition links remain internal.
 * New registration/file bindings are intentionally absent. Library Part/Package identities remain
 * canonical; native pseudo construction may notify Part's computed placement-count listeners.
 */
final class NativeReplacementJob {
    static final int MAX_OBJECTS=100000,MAX_LOCATIONS=1000,MAX_PLACEMENTS=10000,MAX_PATH_SEGMENTS=10000;
    private static final String[] PSEUDO_PROPERTIES={"location","side","id","part","type","placementsHolder","placement","child"};
    static final class Candidate implements AutoCloseable {
        private final Configuration config;private final Job original,job;private final Snapshot before,after;
        private final Builder builder;private final Map<String,Object> mapping;private boolean closed,published;
        private Candidate(Configuration c,Job original,Job fresh,Snapshot before,Snapshot after,Builder builder)throws Exception {
            config=c;this.original=original;job=fresh;this.before=before;this.after=after;this.builder=builder;
            List<Object> locations=new ArrayList<>(),placements=new ArrayList<>();for(PlacementsHolderLocation<?> old:original.getBoardAndPanelLocations()){
                PlacementsHolderLocation<?> next=(PlacementsHolderLocation<?>)builder.copies.get(old);locations.add(map("source_location_id",old.getUniqueId(),"replacement_location_id",next.getUniqueId(),"source_definition",before.definitionId(old.getPlacementsHolder().getDefinition()),"replacement_definition",after.definitionId(next.getPlacementsHolder().getDefinition())));
                for(Placement p:old.getPlacementsHolder().getPlacements())placements.add(map("location_id",old.getUniqueId(),"placement_id",p.getId(),"part_id",p.getPart()==null?null:p.getPart().getId(),"source_placed",original.retrievePlacedStatus(old,p.getId()),"replacement_placed",false));
            }
            mapping=NativeFaultedJobReplacement.frozen(map("schema_version",1,"origin","current-native-job-detached-copy","source_model_sha256",NativeFaultedJobReplacement.digest(before.shape),"replacement_model_sha256",NativeFaultedJobReplacement.digest(after.shape),"source_history_sha256",NativeFaultedJobReplacement.digest(new LinkedHashMap<>(before.history)),"source_history_entries",before.history.size(),"source_completed_history_entries",before.history.values().stream().filter(Boolean.TRUE::equals).count(),"replacement_history_entries",0,"locations",locations,"placements",placements,"definitions_copied",builder.holderCount,"registration_copied",false,"files_bound",false,"native_job_initialized",false,"physical_load_verified",false));
        }
        Job job(){if(closed)throw new IllegalStateException("Candidate is closed");return job;}
        Map<String,Object> mapping(){return mapping;}
        /** Exact source identity is required before recording a reconstructible transaction. */
        void requireOriginal(Job expected)throws Exception{requireCurrent();if(original!=expected)throw bad("Replacement candidate has a different original native Job");}
        void requireCurrent()throws Exception {
            owner(config);requireCurrentSnapshot();
        }
        /** Read-only post-wrapper comparison; normal construction/publication retains executor ownership. */
        void requireCurrentForPublication(NativeFaultedJobReplacement.Publication publication)throws Exception {
            if(publication==null)throw bad("Exact publication witness required");publication.authorizeCompletedRead(this);if(closed)throw bad("Replacement candidate is closed");
            before.requireCapturedReferences(config);after.requireCapturedReferences(config);
            Snapshot current=new Snapshot(config,original,true),candidate=new Snapshot(config,job,true);
            if(!before.sameSource(current)||!after.sameSource(candidate))throw bad("Original or replacement graph changed before final publication validation");
            disjoint(current.objects,candidate.objects);publication.authorizeCompletedRead(this);
        }
        private void requireCurrentSnapshot()throws Exception {
            if(closed)throw bad("Replacement candidate is closed");Snapshot current=new Snapshot(config,original),candidate=new Snapshot(config,job);
            if(!before.sameSource(current)||!after.sameSource(candidate))throw bad("Original or unpublished replacement graph changed");
            NativeBoardLoads.requireDetachedReplacement(original,job);disjoint(current.objects,candidate.objects);
        }
        /** Resource ownership transfer only. Bridge still owns journal/permit/validation/admission. */
        void publish()throws Exception {requireCurrent();published=true;}
        @Override public void close(){if(!closed&&!published){builder.cleanup();closed=true;}}
    }
    static Candidate build(Configuration config,Job original)throws Exception {
        owner(config);Snapshot before=new Snapshot(config,original);Builder builder=new Builder(before);try {
            Job fresh=builder.build(original);Snapshot after=new Snapshot(config,fresh);if(!NativeFaultedJobReplacement.same(before.shape,after.shape)||!after.history.isEmpty())throw bad("Native replacement lost source content or acquired placed history");
            NativeBoardLoads.requireDetachedReplacement(original,fresh);disjoint(before.objects,after.objects);Candidate result=new Candidate(config,original,fresh,before,after,builder);result.requireCurrent();return result;
        }catch(Exception|Error failure){builder.cleanup();throw failure;}
    }
    private static void owner(Configuration config)throws Exception {Machine m=config==null?null:config.getMachine();if(config==null||Configuration.get()!=config||m==null||!m.isTask(Thread.currentThread()))throw bad("Current native configuration and executor ownership required");}
    private static void disjoint(Set<Object> old,Set<Object> fresh)throws Exception {for(Object o:fresh)if(old.contains(o))throw bad("Mutable old native object leaked into replacement");}
    /** Pinned native field read: never initialize OpenPnP's lazily cached transform. */
    static AffineTransform transformForCompletedRead(PlacementsHolderLocation<?> location)throws Exception {
        Object value=readNativeField(PlacementsHolderLocation.class,"localToParentTransform",location);
        if(!(value instanceof AffineTransform))throw bad("Final publication validation requires the exact previously cached native transform");return (AffineTransform)value;
    }
    private static Object readNativeField(Class<?> type,String name,Object object)throws Exception {
        java.lang.reflect.Field field=type.getDeclaredField(name);if(!field.trySetAccessible())throw bad("Pinned native read-only field is unavailable: "+name);return field.get(object);
    }
    private static final class Snapshot {
        final boolean completedRead;final PanelLocation root;
        final Job job;final Map<String,Boolean> history;final Map<String,Object> shape,sourceState;
        final Set<Object> objects=Collections.newSetFromMap(new IdentityHashMap<>());
        final IdentityHashMap<PlacementsHolder<?>,Integer> definitions=new IdentityHashMap<>();final Set<PlacementsHolder<?>> visitingDefinitions=Collections.newSetFromMap(new IdentityHashMap<>());
        final IdentityHashMap<Placement,Panel> pseudoOwners=new IdentityHashMap<>();
        final IdentityHashMap<Part,org.openpnp.model.Package> parts=new IdentityHashMap<>();final IdentityHashMap<Object,List<Object>> nativeLinks=new IdentityHashMap<>();final IdentityHashMap<Object,Map<String,Object>> nativeValues=new IdentityHashMap<>();
        final List<Object> locations=new ArrayList<>(),definitionRows=new ArrayList<>(),sourceRows=new ArrayList<>();
        final Set<PlacementsHolder<?>> expandedHolders=Collections.newSetFromMap(new IdentityHashMap<>());
        final Set<Placement> expandedPlacements=Collections.newSetFromMap(new IdentityHashMap<>());int placementCount;
        Snapshot(Configuration config,Job job)throws Exception {this(config,job,false);}
        Snapshot(Configuration config,Job job,boolean completedRead)throws Exception {
            this.completedRead=completedRead;this.job=job;if(job==null||job.getClass()!=Job.class)throw bad("Exact native Job required");root=job.getRootPanelLocation();if(completedRead)NativeBoardLoads.validateCandidateForCompletedRead(job);else NativeBoardLoads.validateCandidate(job);if(job.getRootPanelLocation().getDefinition()!=job.getRootPanelLocation()||job.getRootPanelLocation().getPanel().getDefinition()!=job.getRootPanelLocation().getPanel())throw bad("Native inline root must own its definitions");history=Collections.unmodifiableMap(new TreeMap<>(job.getPlacedStatusSnapshot()));if(history.size()>100000)throw bad("Old history capacity reached");
            collect(job.getRootPanelLocation());objects.add(job);captureBindings();for(PlacementsHolderLocation<?> loc:job.getBoardAndPanelLocations()){
                if(locations.size()>=MAX_LOCATIONS||!expandedHolders.add(loc.getPlacementsHolder()))throw bad("Aliased or excessive expanded holder instances");for(Placement p:loc.getPlacementsHolder().getPlacements())if(!expandedPlacements.add(p)||++placementCount>MAX_PLACEMENTS)throw bad("Aliased or excessive expanded placements");
                List<Object> overrides=new ArrayList<>();for(Placement p:loc.getPlacementsHolder().getPlacements())overrides.add(map("id",p.getId(),"enabled",job.retrieveEnabledState(loc,p),"error",job.retrieveErrorHandlingState(loc,p).name()));
                locations.add(map("id",loc.getUniqueId(),"class",loc.getClass().getSimpleName(),"definition",definitionId(loc.getPlacementsHolder().getDefinition()),"location",pose(loc.getLocation()),"side",loc.getSide().name(),"enabled",loc.isLocallyEnabled(),"check_fiducials",loc.isCheckFiducials(),"stored_enabled",job.retrieveEnabledState(loc,null),"stored_check_fiducials",job.retrieveCheckFiducialsState(loc),"placement_overrides",overrides,"holder",holderValues(loc.getPlacementsHolder())));
                sourceRows.add(map("id",loc.getUniqueId(),"file",loc.getFileName(),"dirty",loc.isDirty(),"holder_file",loc.getPlacementsHolder().getFile()==null?null:loc.getPlacementsHolder().getFile().toString(),"holder_dirty",loc.getPlacementsHolder().isDirty(),"registration",transform(localTransform(loc)),"registration_status",loc.getPlacementsTransformStatus().name()));
            }
            for(Part part:parts.keySet())if(config.getPart(part.getId())!=part||part.getPackage()==null||config.getPackage(part.getPackage().getId())!=part.getPackage()||parts.get(part)!=part.getPackage())throw bad("Native library Part/Package identity differs");
            shape=NativeFaultedJobReplacement.frozen(map("error_handling",job.getErrorHandling().name(),"root_name",job.getRootPanelLocation().getPanel().getName(),"root_dimensions",pose(job.getRootPanelLocation().getPanel().getDimensions()),"locations",locations,"definitions",definitionRows));
            sourceState=NativeFaultedJobReplacement.frozen(map("job_file",job.getFile()==null?null:job.getFile().toString(),"job_dirty",job.isDirty(),"nodes",sourceRows));
        }
        int definitionId(PlacementsHolder<?> holder)throws Exception {
            if(holder==null||holder.getDefinition()!=holder)throw bad("Native definition must be a self-defined exact holder");Integer id=definitions.get(holder);if(id!=null){if(visitingDefinitions.contains(holder))throw bad("Cyclic native definition hierarchy");return id;}visitingDefinitions.add(holder);int index=definitions.size();if(index>=1000)throw bad("Definition capacity reached");definitions.put(holder,index);definitionRows.add(null);
            List<Object> children=new ArrayList<>();if(holder.getClass()==Panel.class)for(PlacementsHolderLocation<?> child:((Panel)holder).getChildren())children.add(map("id",child.getId(),"class",child.getClass().getSimpleName(),"definition",definitionId(child.getPlacementsHolder().getDefinition()),"location",pose(child.getLocation()),"side",child.getSide().name(),"enabled",child.isLocallyEnabled(),"check_fiducials",child.isCheckFiducials()));
            definitionRows.set(index,map("index",index,"holder",holderValues(holder),"children",children));visitingDefinitions.remove(holder);return index;
        }
        Map<String,Object> holderValues(PlacementsHolder<?> h)throws Exception {
            List<Object> placements=new ArrayList<>(),pseudo=new ArrayList<>(),pads=new ArrayList<>();for(Placement p:h.getPlacements())placements.add(placementValues(p));if(h.getClass()==Panel.class)for(Placement p:((Panel)h).getPseudoPlacements())pseudo.add(placementValues(p));if(h.getClass()==Board.class)for(BoardPad pad:((Board)h).getSolderPastePads())pads.add(padValues(pad));
            return map("class",h.getClass().getSimpleName(),"name",h.getName(),"dimensions",pose(h.getDimensions()),"profile",pathValues(h.getProfile()),"placements",placements,"pseudo",pseudo,"pads",pads);
        }
        void collect(Object value)throws Exception {
            if(value==null||!objects.add(value))return;if(objects.size()>MAX_OBJECTS)throw bad("Mutable ownership closure exceeds capacity");
            if(value instanceof PlacementsHolderLocation){PlacementsHolderLocation<?> loc=(PlacementsHolderLocation<?>)value;if(loc.getClass()!=BoardLocation.class&&loc.getClass()!=PanelLocation.class)throw bad("Custom native location is unsupported");if(loc.getDefinition()==null||loc.getDefinition().getDefinition()!=loc.getDefinition())throw bad("Native location definition must be self-defined");Set<PanelLocation> ancestors=Collections.newSetFromMap(new IdentityHashMap<>());for(PanelLocation parent=loc.getParent();parent!=null;parent=parent.getParent())if(!ancestors.add(parent)||ancestors.size()>1000)throw bad("Cyclic or excessive native parent chain");collect(loc.getDefinition());collect(loc.getParent());collect(loc.getPlacementsHolder());collect(localTransform(loc));}
            else if(value instanceof PlacementsHolder){PlacementsHolder<?> holder=(PlacementsHolder<?>)value;if(holder.getClass()!=Board.class&&holder.getClass()!=Panel.class)throw bad("Custom native holder is unsupported");collect(holder.getDefinition());GeometricPath2D profile=holder.getProfile();if(profile==holder.getProfile()){pathValues(profile);collect(profile);}for(Placement p:holder.getPlacements())collect(p);if(holder.getClass()==Panel.class){Panel panel=(Panel)holder;for(Placement p:panel.getPseudoPlacements()){pseudoOwners.putIfAbsent(p,panel);collect(p);}for(PlacementsHolderLocation<?> child:panel.getChildren())collect(child);}else for(BoardPad pad:((Board)holder).getSolderPastePads()){padValues(pad);collect(pad);collect(pad.getPad());}}
            else if(value instanceof Placement){Placement p=(Placement)value;if(p.getClass()!=Placement.class&&p.getClass()!=PseudoPlacement.class)throw bad("Custom native placement is unsupported");placementValues(p);if(p.getDefinition()==null||p.getDefinition().getDefinition()!=p.getDefinition())throw bad("Native placement definition must be self-defined");collect(p.getDefinition());if(p.getPart()!=null)parts.put(p.getPart(),p.getPart().getPackage());}
        }
        void captureBindings()throws Exception {
            for(Object value:objects){List<Object> refs=new ArrayList<>();Map<String,Object> values=null;
                if(value instanceof PlacementsHolderLocation){PlacementsHolderLocation<?> loc=(PlacementsHolderLocation<?>)value;refs.add(loc.getDefinition());refs.add(loc.getParent());refs.add(loc.getPlacementsHolder());refs.add(localTransform(loc));values=map("id",loc.getId(),"pose",pose(loc.getLocation()),"side",loc.getSide().name(),"enabled",loc.isLocallyEnabled(),"check",loc.isCheckFiducials(),"file",loc.getFileName(),"dirty",loc.isDirty(),"registration",transform(localTransform(loc)),"registration_status",loc.getPlacementsTransformStatus().name());}
                else if(value instanceof PlacementsHolder){PlacementsHolder<?> h=(PlacementsHolder<?>)value;refs.add(h.getDefinition());GeometricPath2D profile=h.getProfile();if(profile==h.getProfile())refs.add(profile);refs.addAll(h.getPlacements());if(h instanceof Panel){refs.addAll(((Panel)h).getChildren());refs.addAll(((Panel)h).getPseudoPlacements());}else refs.addAll(((Board)h).getSolderPastePads());values=map("holder",holderValues(h),"file",h.getFile()==null?null:h.getFile().toString(),"dirty",h.isDirty());}
                else if(value instanceof Placement){Placement p=(Placement)value;refs.add(p.getDefinition());refs.add(p.getPart());values=map("placement",placementValues(p),"dirty",p.isDirty());}
                else if(value instanceof BoardPad){BoardPad p=(BoardPad)value;refs.add(p.getPad());values=padValues(p);}
                else if(value instanceof GeometricPath2D)values=pathValues((GeometricPath2D)value);else if(value instanceof AffineTransform)values=map("matrix",transform((AffineTransform)value));
                nativeLinks.put(value,refs);if(values!=null)nativeValues.put(value,NativeFaultedJobReplacement.frozen(values));
            }
        }
        private AffineTransform localTransform(PlacementsHolderLocation<?> location)throws Exception {return completedRead?transformForCompletedRead(location):location.getLocalToParentTransform();}
        /** Compare only captured nodes and direct references first. Never follow a newly inserted
         * root/child/definition or initialize a cleared cache on a refusal path. */
        void requireCapturedReferences(Configuration config)throws Exception {
            if(Configuration.get()!=config||job.getRootPanelLocation()!=root)throw bad("Native root or configuration changed before final publication validation");
            for(Object value:objects){
                List<Object> refs=new ArrayList<>();
                if(value instanceof PlacementsHolderLocation){PlacementsHolderLocation<?> loc=(PlacementsHolderLocation<?>)value;refs.add(loc.getDefinition());refs.add(loc.getParent());refs.add(loc.getPlacementsHolder());refs.add(transformForCompletedRead(loc));}
                else if(value instanceof PlacementsHolder){PlacementsHolder<?> holder=(PlacementsHolder<?>)value;refs.add(holder.getDefinition());Object profile=readNativeField(PlacementsHolder.class,"profile",holder);if(profile!=null)refs.add(profile);refs.addAll(holder.getPlacements());if(holder instanceof Panel){refs.addAll(((Panel)holder).getChildren());refs.addAll(((Panel)holder).getPseudoPlacements());}else refs.addAll(((Board)holder).getSolderPastePads());}
                else if(value instanceof Placement){Placement placement=(Placement)value;refs.add(placement.getDefinition());refs.add(placement.getPart());}
                else if(value instanceof BoardPad)refs.add(((BoardPad)value).getPad());
                List<Object> expected=nativeLinks.get(value);if(expected==null||expected.size()!=refs.size())throw bad("Native reference membership changed before final publication validation");
                for(int i=0;i<refs.size();i++)if(refs.get(i)!=expected.get(i))throw bad("Native reference identity changed before final publication validation");
            }
            for(Map.Entry<Part,org.openpnp.model.Package> entry:parts.entrySet())if(config.getPart(entry.getKey().getId())!=entry.getKey()||entry.getKey().getPackage()!=entry.getValue()||config.getPackage(entry.getValue().getId())!=entry.getValue())throw bad("Canonical native library identity changed before final publication validation");
        }
        boolean sameSource(Snapshot other)throws Exception {
            if(!objects.equals(other.objects)||!parts.equals(other.parts)||!history.equals(other.history)||!NativeFaultedJobReplacement.same(shape,other.shape)||!NativeFaultedJobReplacement.same(sourceState,other.sourceState))return false;
            for(Object object:objects){List<Object> a=nativeLinks.get(object),b=other.nativeLinks.get(object);if(b==null||a.size()!=b.size())return false;for(int i=0;i<a.size();i++)if(a.get(i)!=b.get(i))return false;Map<String,Object> v=nativeValues.get(object),w=other.nativeValues.get(object);if(v==null?w!=null:w==null||!NativeFaultedJobReplacement.same(v,w))return false;}return true;
        }
    }
    private static final class Edge {final AbstractModelObject source;final PropertyChangeListener listener;final String property;Edge(AbstractModelObject s,PropertyChangeListener l,String p){source=s;listener=l;property=p;}void remove(){if(property==null){while(source.isListener(listener))source.removePropertyChangeListener(listener);}else while(source.isListener(property,listener))source.removePropertyChangeListener(property,listener);}}
    private static final class Builder {
        final Snapshot source;final IdentityHashMap<Object,Object> copies=new IdentityHashMap<>();final List<Edge> edges=new ArrayList<>();final List<Panel> deferredPseudo=new ArrayList<>();final List<PlacementsHolder<?>> deferredHolderLinks=new ArrayList<>();final Set<PlacementsHolder<?>> childrenReady=Collections.newSetFromMap(new IdentityHashMap<>());int holderCount;
        Builder(Snapshot source){this.source=source;}
        <T>T own(T value){if(value instanceof AbstractModelObject&&value instanceof PropertyChangeListener)edges.add(new Edge((AbstractModelObject)value,(PropertyChangeListener)value,null));return value;}
        void edge(AbstractModelObject from,Object to,String property){if(to instanceof PropertyChangeListener)edges.add(new Edge(from,(PropertyChangeListener)to,property));}
        Job build(Job original)throws Exception {
            Job fresh=own(new Job());copies.put(original,fresh);PanelLocation oldRoot=original.getRootPanelLocation(),newRoot=own(fresh.getRootPanelLocation());copies.put(oldRoot,newRoot);copies.put(oldRoot.getPanel(),own(newRoot.getPanel()));holderCount++;
            fresh.setErrorHandling(original.getErrorHandling());newRoot.setId(oldRoot.getId());newRoot.setLocation(copyPose(oldRoot.getLocation()));fillHolder(oldRoot.getPanel(),newRoot.getPanel());PanelLocation.setParentsOfAllDescendants(newRoot);finishPseudo();
            for(PlacementsHolderLocation<?> old:original.getBoardAndPanelLocations()){
                PlacementsHolderLocation<?> loc=(PlacementsHolderLocation<?>)copies.get(old);fresh.storeEnabledState(loc,null,original.retrieveEnabledState(old,null));fresh.storeCheckFiducialsState(loc,original.retrieveCheckFiducialsState(old));
                for(Placement p:old.getPlacementsHolder().getPlacements()){Placement next=(Placement)copies.get(p);fresh.storeEnabledState(loc,next,original.retrieveEnabledState(old,p));fresh.storeErrorHandlingState(loc,next,original.retrieveErrorHandlingState(old,p));}
            }
            fresh.setFile(null);Set<Object> owned=Collections.newSetFromMap(new IdentityHashMap<>());owned.addAll(copies.values());for(Object value:owned)if(value instanceof AbstractModelObject&&value instanceof PropertyChangeListener){AbstractModelObject object=(AbstractModelObject)value;PropertyChangeListener self=(PropertyChangeListener)value;while(object.isListener(self))object.removePropertyChangeListener(self);object.addPropertyChangeListener(self);}return fresh;
        }
        PlacementsHolder<?> holder(PlacementsHolder<?> source)throws Exception {Object cached=copies.get(source);if(cached!=null)return(PlacementsHolder<?>)cached;PlacementsHolder<?> fresh=source.getClass()==Board.class?own(new Board()):own(new Panel());copies.put(source,fresh);if(++holderCount>10000)throw bad("Replacement holder capacity reached");fillHolder(source,fresh);return fresh;}
        @SuppressWarnings({"rawtypes","unchecked"})void fillHolder(PlacementsHolder<?> source,PlacementsHolder<?> fresh)throws Exception {
            PlacementsHolder<?> definition=source.getDefinition()==source?fresh:holder(source.getDefinition());fresh.setName(source.getName());fresh.setDimensions(copyPose(source.getDimensions()));GeometricPath2D profile=source.getProfile();if(profile==source.getProfile())fresh.setProfile(profile.convertToUnits(profile.getUnits()));fresh.setFile(null);
            for(Placement p:source.getPlacements()){Placement copy=placement(p);fresh.addPlacement(copy);edge(copy,fresh,null);}
            if(source.getClass()==Board.class)for(BoardPad pad:((Board)source).getSolderPastePads()){BoardPad copy=copyPad(pad);copies.put(pad,copy);copies.put(pad.getPad(),copy.getPad());((Board)fresh).addSolderPastePad(copy);edge(copy,fresh,null);}
            else {Panel a=(Panel)source,b=(Panel)fresh;for(PlacementsHolderLocation<?> child:a.getChildren()){PlacementsHolderLocation<?> copy=location(child);b.addChild(copy);edge(copy,b,null);}childrenReady.add(source);deferredPseudo.add(a);}
            if(fresh.getDefinition()!=definition){((PlacementsHolder)fresh).setDefinition(definition);definition.removePropertyChangeListener(fresh);deferredHolderLinks.add(fresh);}edge(definition,fresh,null);
        }
        @SuppressWarnings({"rawtypes","unchecked"})PlacementsHolderLocation<?> location(PlacementsHolderLocation<?> source)throws Exception {
            Object cached=copies.get(source);if(cached!=null)return(PlacementsHolderLocation<?>)cached;PlacementsHolderLocation<?> fresh=source.getClass()==BoardLocation.class?own(new BoardLocation()):own(new PanelLocation());copies.put(source,fresh);
            fresh.setId(source.getId());fresh.setLocation(copyPose(source.getLocation()));fresh.setSide(source.getSide());fresh.setLocallyEnabled(source.isLocallyEnabled());fresh.setCheckFiducials(source.isCheckFiducials());fresh.setPlacementsHolder(holder(source.getPlacementsHolder()));fresh.setFileName(null);fresh.setLocalToParentTransform(null);
            if(source.getParent()!=null)fresh.setParent((PanelLocation)location(source.getParent()));PlacementsHolderLocation<?> definition=source.getDefinition()==source?fresh:location(source.getDefinition());((PlacementsHolderLocation)fresh).setDefinition(definition);edge(definition,fresh,null);return fresh;
        }
        void finishPseudo()throws Exception {
            // Keep native definition references correct for PseudoPlacement branch ownership, but
            // postpone definition listeners until all lists exist: native indexed events would
            // otherwise manufacture extra untracked Placement instances in dependent panels.
            for(Panel old:deferredPseudo){Panel fresh=(Panel)copies.get(old);IdentifiableList<Placement> list=new IdentifiableList<>();for(Placement p:old.getPseudoPlacements()){Placement copy=placement(p);list.add(copy);copy.addPropertyChangeListener(fresh);edge(copy,fresh,null);}fresh.setPseudoPlacements(list);}
            for(PlacementsHolder<?> fresh:deferredHolderLinks)fresh.getDefinition().addPropertyChangeListener(fresh);
        }
        Placement placement(Placement source)throws Exception {
            Object cached=copies.get(source);if(cached!=null)return(Placement)cached;Placement copy;
            if(source.getClass()==PseudoPlacement.class){Panel oldOwner=this.source.pseudoOwners.get(source);if(oldOwner==null)throw bad("Pseudo-placement owner missing");Panel freshOwner=(Panel)holder(oldOwner);if(!childrenReady.contains(oldOwner))throw bad("Cyclic or unresolved pseudo-placement dependency");copy=own(new PseudoPlacement(freshOwner,source.getId()));copies.put(source,copy);pseudoEdges(freshOwner,copy);copyValues(source,copy);}
            else {copy=own(new Placement(source));copies.put(source,copy);edge(source.getDefinition(),copy,null);copy.setDefinition(copy);edge(copy,copy,null);copy.setLocation(copyPose(source.getLocation()));}
            if(source.getDefinition()!=source){Placement definition=placement(source.getDefinition());copy.setDefinition(definition);edge(definition,copy,null);}return copy;
        }
        void pseudoEdges(Panel panel,Placement pseudo)throws Exception {Pair<List<PlacementsHolderLocation<?>>,Placement> pair=panel.getDescendantPlacement(pseudo.getId());if(pair==null)throw bad("Pseudo reference did not resolve inside replacement");for(String key:PSEUDO_PROPERTIES)edge(pair.second.getDefinition(),pseudo,key);for(PlacementsHolderLocation<?> loc:pair.first){for(String key:PSEUDO_PROPERTIES){edge(loc.getDefinition(),pseudo,key);edge(loc.getDefinition().getPlacementsHolder().getDefinition(),pseudo,key);}}}
        void cleanup(){for(int i=edges.size()-1;i>=0;i--)edges.get(i).remove();edges.clear();}
    }
    private static void copyValues(Placement a,Placement b){b.setLocation(copyPose(a.getLocation()));b.setSide(a.getSide());b.setType(a.getType());if(b.getPart()!=a.getPart())b.setPart(a.getPart());b.setEnabled(a.isEnabled());b.setErrorHandling(a.getErrorHandling());b.setComments(a.getComments());b.setRank(a.getRank());}
    private static BoardPad copyPad(BoardPad p)throws Exception {padValues(p);BoardPad out=new BoardPad();out.setName(p.getName());out.setLocation(copyPose(p.getLocation()));out.setType(p.getType());out.setSide(p.getSide());out.setPad(p.getPad().convertToUnits(p.getPad().getUnits()));if(p.getPad() instanceof Pad.RoundRectangle)((Pad.RoundRectangle)out.getPad()).setRoundness(((Pad.RoundRectangle)p.getPad()).getRoundness());if(out.getPad()==p.getPad())throw bad("Pad shape copy aliases source");return out;}
    private static Map<String,Object> placementValues(Placement p)throws Exception {if(p.getId()==null||p.getId().length()>512||p.getComments()!=null&&p.getComments().length()>2048||p.getSide()==null||p.getType()==null||p.getErrorHandling()==null)throw bad("Invalid native placement fields");return map("id",p.getId(),"location",pose(p.getLocation()),"part",p.getPart()==null?null:p.getPart().getId(),"side",p.getSide().name(),"type",p.getType().name(),"enabled",p.isEnabled(),"error",p.getErrorHandling().name(),"comments",p.getComments(),"rank",p.getRank());}
    private static Map<String,Object> padValues(BoardPad p)throws Exception {if(p==null||p.getClass()!=BoardPad.class||p.getPad()==null||p.getPad().getUnits()==null||p.getSide()==null||p.getType()==null)throw bad("Exact native pad metadata required");Pad shape=p.getPad();Map<String,Object> dimensions;if(shape.getClass()==Pad.Circle.class)dimensions=map("radius",finite(((Pad.Circle)shape).getRadius()));else if(shape.getClass()==Pad.Ellipse.class)dimensions=map("width",finite(((Pad.Ellipse)shape).getWidth()),"height",finite(((Pad.Ellipse)shape).getHeight()));else if(shape.getClass()==Pad.RoundRectangle.class)dimensions=map("width",finite(((Pad.RoundRectangle)shape).getWidth()),"height",finite(((Pad.RoundRectangle)shape).getHeight()),"roundness",finite(((Pad.RoundRectangle)shape).getRoundness()));else throw bad("Unsupported native pad shape");return map("name",p.getName(),"location",pose(p.getLocation()),"side",p.getSide().name(),"type",p.getType().name(),"shape",shape.getClass().getSimpleName(),"units",shape.getUnits().name(),"dimensions",dimensions);}
    private static Map<String,Object> pathValues(GeometricPath2D path)throws Exception {if(path==null||path.getUnits()==null)throw bad("Native path units required");List<Object> segments=new ArrayList<>();PathIterator i=path.getPathIterator(null);double[] p=new double[6];while(!i.isDone()){if(segments.size()>=MAX_PATH_SEGMENTS)throw bad("Native outline exceeds bounded path");int type=i.currentSegment(p),n=type==PathIterator.SEG_CLOSE?0:type==PathIterator.SEG_QUADTO?4:type==PathIterator.SEG_CUBICTO?6:2;List<Object> values=new ArrayList<>();for(int x=0;x<n;x++)values.add(finite(p[x]));segments.add(map("type",type,"points",values));i.next();}return map("units",path.getUnits().name(),"winding",path.getWindingRule(),"segments",segments);}
    private static List<Double> transform(AffineTransform value)throws Exception {if(value==null)return null;double[] matrix=new double[6];value.getMatrix(matrix);List<Double> result=new ArrayList<>();for(double number:matrix)result.add(finite(number));return result;}
    private static double finite(double value)throws IOException {if(!Double.isFinite(value))throw bad("Finite native geometry required");return value;}
    private static Location copyPose(Location p){return new Location(p.getUnits(),p.getX(),p.getY(),p.getZ(),p.getRotation());}
    private static Map<String,Object> pose(Location p)throws Exception {if(p==null||p.getUnits()==null)throw bad("Native location units required");return map("units",p.getUnits().name(),"x",finite(p.getX()),"y",finite(p.getY()),"z",finite(p.getZ()),"rotation",finite(p.getRotation()));}
    private static Map<String,Object> map(Object...v){return NativeFaultedJobReplacement.map(v);}
    private static IOException bad(String message){return new IOException(message);}
}
