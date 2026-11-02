/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeReplacementContinuationPublicationTest.*;
import java.awt.geom.AffineTransform;
import java.beans.PropertyChangeListener;
import java.io.IOException;
import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;

/** Real native caches and mutable graphs; synthetic original fault, successful wrapper and exact
 * host selection. The final checks run off the native executor and may neither repair nor hydrate. */
public final class NativeReplacementContinuationReadOnlyTest {
    static final List<Map<String,Object>> results = new ArrayList<>();
    static int assertions, refusals, nativeChanges;
    static void check(boolean value, String why) { assertions++; if (!value) throw new AssertionError(why); }
    static Field field(Class<?> type, String name) throws Exception { Field f=type.getDeclaredField(name);f.setAccessible(true);return f; }
    static Object transform(PlacementsHolderLocation<?> location) throws Exception { return field(PlacementsHolderLocation.class,"localToParentTransform").get(location); }
    static Object tips(org.openpnp.model.Package pkg) throws Exception { return field(org.openpnp.model.Package.class,"compatibleNozzleTips").get(pkg); }
    static boolean verifies(Ready ready,Map<String,Object> dispositions)throws Exception {
        try { return ready.fixture.e.replacement.verifiedContinuation(ready.fresh.faultCapture.payload,dispositions,false); }
        catch (IOException refused) { return false; }
    }
    interface Checked { void run() throws Exception; }
    static void refuses(Checked action,String why)throws Exception {
        try { action.run(); throw new AssertionError("Accepted "+why); }
        catch(IOException | Bridge.Fault expected) { assertions++;refusals++; }
    }
    static boolean scalar(Object o){return o==null||o instanceof String||o instanceof Number||o instanceof Boolean||o instanceof Character||o instanceof Enum;}
    static boolean same(Object a,Object b){return scalar(a)&&scalar(b)?Objects.equals(a,b):a==b;}
    /** Shallow copies of native fields and collection members; no model getters. Captured model
     * references are walked once before verification, with identity cycle protection. */
    static final class RawState implements AutoCloseable {
        final IdentityHashMap<Object,Map<Field,Object>> values=new IdentityHashMap<>();
        final IdentityHashMap<Object,List<Object>> members=new IdentityHashMap<>();
        final IdentityHashMap<AffineTransform,double[]> transforms=new IdentityHashMap<>();
        final List<AbstractModelObject> observed=new ArrayList<>(); int notifications;
        final PropertyChangeListener listener=e->notifications++;
        RawState(Object... roots)throws Exception {for(Object root:roots)capture(root);for(Object obj:values.keySet())if(obj instanceof AbstractModelObject){AbstractModelObject model=(AbstractModelObject)obj;model.addPropertyChangeListener(listener);observed.add(model);}}
        void capture(Object obj)throws Exception {
            if(obj==null||scalar(obj)||values.containsKey(obj))return;
            if(obj instanceof AffineTransform){double[] matrix=new double[6];((AffineTransform)obj).getMatrix(matrix);transforms.put((AffineTransform)obj,matrix);return;}
            if(obj instanceof Map){if(members.containsKey(obj))return;List<Object> copy=new ArrayList<>();members.put(obj,copy);for(var e:((Map<?,?>)obj).entrySet()){copy.add(e.getKey());copy.add(e.getValue());capture(e.getKey());capture(e.getValue());}return;}
            if(obj instanceof Collection){if(members.containsKey(obj))return;List<Object> copy=new ArrayList<>((Collection<?>)obj);members.put(obj,copy);for(Object member:copy)capture(member);return;}
            String name=obj.getClass().getName();if(!name.startsWith("org.openpnp.model.")&&!name.equals("org.openpnp.machine.reference.feeder.ReferenceTrayFeeder"))return;
            if(values.size()>10000)throw new AssertionError("Fixture raw graph exceeded bound");Map<Field,Object> row=new LinkedHashMap<>();values.put(obj,row);
            for(Class<?> c=obj.getClass();c!=null&&c.getName().startsWith("org.openpnp.");c=c.getSuperclass())for(Field f:c.getDeclaredFields())if(!Modifier.isStatic(f.getModifiers())){f.setAccessible(true);Object value=f.get(obj);row.put(f,value);capture(value);}
        }
        void requireUnchanged()throws Exception {
            check(notifications==0,"Final validation fired no native property notifications");
            for(var row:values.entrySet())for(var f:row.getValue().entrySet())check(same(f.getValue(),f.getKey().get(row.getKey())),"Final validation changed native field "+f.getKey());
            for(var row:members.entrySet()){List<Object> now=new ArrayList<>();if(row.getKey() instanceof Map){for(var e:((Map<?,?>)row.getKey()).entrySet()){now.add(e.getKey());now.add(e.getValue());}}else now.addAll((Collection<?>)row.getKey());check(now.size()==row.getValue().size(),"Final validation changed collection size");for(int i=0;i<now.size();i++)check(same(now.get(i),row.getValue().get(i)),"Final validation changed native collection member identity");}
            for(var row:transforms.entrySet()){double[] now=new double[6];row.getKey().getMatrix(now);check(Arrays.equals(now,row.getValue()),"Final validation changed cached native transform values");}
        }
        public void close(){for(AbstractModelObject model:observed)model.removePropertyChangeListener(listener);}
    }
    static void runCase(String kind)throws Exception {
        int start=assertions;try(Ready ready=new Ready("read-only-"+kind,"definition")) {
            var f=ready.fixture;var publication=begin(ready);ready.install(stage(ready,publication));finish(ready,publication);
            Map<String,Object> dispositions=f.e.replacement.continuationDispositions(publication.publicationId(),"none-present");
            Map<String,Object> terminal=freshOperation(ready.fresh);terminal.put("state","succeeded");terminal.put("native_completion",m("native_wrapper_completed",true,"native_wrapper_succeeded",true,"physical_outcome_verified",false));f.e.append("operation",terminal);f.e.terminalWrappers.add(ready.fresh.operation);
            check(!config.getMachine().isTask(Thread.currentThread())&&!config.getMachine().isBusy(),"Final checks run on a quiescent completion thread outside native executor");
            long baselineBytes=Files.size(f.e.file);Map<String,String> baselineFiles=configurationFiles();
            try(RawState baseline=new RawState(f.e.oldJob,f.e.freshJob,tray,otherTray)) {
                check(verifies(ready,dispositions),"Exact published state verifies before deliberate drift");
                f.e.candidate.requireCurrentForPublication(publication);f.e.material.requireReadyForPublication(publication);f.e.boards.requireReadyForPublication(f.e.freshJob,publication);
                baseline.requireUnchanged();
                check(Files.size(f.e.file)==baselineBytes&&baselineFiles.equals(configurationFiles()),"Successful final validation is also read-only");
            }
            PlacementsHolderLocation<?> affected=kind.startsWith("old-")?f.e.oldJob.getBoardLocations().get(0):f.e.freshJob.getBoardLocations().get(0);
            if(kind.contains("virtual"))affected=f.e.freshJob.getRootPanelLocation();
            final PlacementsHolderLocation<?> location=affected;Object originalTransform=transform(location);
            org.openpnp.model.Package pkg=tray.getPart().getPackage();Object originalTips=tips(pkg);
            List<Object> extra=new ArrayList<>();Checked cleanup=()->{};
            if(kind.equals("package-cache")) {check(originalTips instanceof Set,"Canonical tip cache was initialized during native publication");task(()->{field(org.openpnp.model.Package.class,"compatibleNozzleTips").set(pkg,null);nativeChanges++;return null;});cleanup=()->field(org.openpnp.model.Package.class,"compatibleNozzleTips").set(pkg,originalTips);}
            else if(kind.equals("foreign-package")) {org.openpnp.model.Package foreign=new org.openpnp.model.Package(pkg.getId());check(tips(foreign)==null,"Foreign same-ID package begins uncached");extra.add(foreign);task(()->{tray.getPart().setPackage(foreign);nativeChanges++;return null;});cleanup=()->tray.getPart().setPackage(pkg);}
            else if(kind.equals("insert-board")||kind.equals("insert-panel")) {
                BoardLocation child=new BoardLocation(new Board());child.setId("new-child");PlacementsHolderLocation<?> inserted=child;extra.add(child);
                if(kind.equals("insert-panel")){PanelLocation panel=new PanelLocation(new Panel());panel.setId("new-panel");panel.addChild(child);inserted=panel;extra.add(panel);}
                final PlacementsHolderLocation<?> node=inserted;task(()->{f.e.freshJob.addBoardOrPanelLocation(node);nativeChanges++;return null;});check(transform(node)==null&&transform(child)==null,"New native child graph is uncached before final validation");cleanup=()->{ // Test-only teardown: native removeChild.dispose requires a GUI MainFrame.
                    Panel parent=f.e.freshJob.getRootPanelLocation().getPanel();
                    ((List<?>)field(Panel.class,"children").get(parent)).remove(node);
                    node.removePropertyChangeListener(parent);node.setParent(null);
                };
            } else if(kind.equals("foreign-root")) {
                PanelLocation root=f.e.freshJob.getRootPanelLocation(),foreign=new PanelLocation(new Panel());extra.add(foreign);task(()->{field(Job.class,"rootPanelLocation").set(f.e.freshJob,foreign);nativeChanges++;return null;});check(transform(foreign)==null,"Foreign native root is uncached");cleanup=()->field(Job.class,"rootPanelLocation").set(f.e.freshJob,root);
            } else {
                check(originalTransform instanceof AffineTransform,"Published native location starts with cached transform");
                Location pose=location.getLocation();task(()->{if(kind.equals("fresh-location-move"))location.setLocation(pose.derive(pose.getX()+1,null,null,null));else location.setLocalToParentTransform(null);nativeChanges++;return null;});
                check(transform(location)==null,"Actual native setter invalidated cached transform");cleanup=()->field(PlacementsHolderLocation.class,"localToParentTransform").set(location,originalTransform);
            }
            long bytes=Files.size(f.e.file);Map<String,String> files=configurationFiles();Map<String,Integer> counts=counters();Map<String,Object> original=immutableOriginal(f.e);
            try(RawState raw=new RawState(f.e.oldJob,f.e.freshJob,tray,otherTray,extra)) {
                try {
                    check(!verifies(ready,dispositions),"Final verifier refuses changed native graph/cache: "+kind);refusals++;
                    if(kind.equals("package-cache")||kind.equals("foreign-package"))refuses(()->f.e.material.requireReadyForPublication(publication),"direct final material cache check");
                    else if(kind.equals("board-direct-virtual"))refuses(()->f.e.boards.requireReadyForPublication(f.e.freshJob,publication),"direct final board cache check");
                    else refuses(()->f.e.candidate.requireCurrentForPublication(publication),"direct final candidate reference/cache check");
                    raw.requireUnchanged();
                    check(Files.size(f.e.file)==bytes&&files.equals(configurationFiles()),"Refusal performs no journal write or configuration save");
                    check(counts.equals(counters()),"Refusal performs no native tray index change");
                    check(NativeFaultedJobReplacement.same(original,immutableOriginal(f.e)),"Refusal preserves all original operation/action records");
                    ready.preserved();
                    results.add(m("kind",kind,"assertions",assertions-start,"raw_native_objects_checked",raw.values.size(),"raw_collections_checked",raw.members.size(),"native_property_notifications",raw.notifications,"accepted",false));
                } finally { /* Cleanup is deliberately outside the measured read-only boundary. */ }
            } finally {Checked restore=cleanup;task(()->{restore.run();return null;});}
        }
    }
    public static void main(String[] args)throws Exception {
        initialize(Path.of(args[0]));Throwable error=null;
        try {for(String kind:List.of("fresh-virtual","fresh-board","old-board","fresh-location-move","insert-board","insert-panel","foreign-root","package-cache","foreign-package","board-direct-virtual"))runCase(kind);}
        catch(Throwable failure){error=failure;failure.printStackTrace();}
        finally {config.getMachine().close();Map<String,Object> proof=m("passed",error==null,"assertions",assertions,"refusals",refusals,"fixture_assertions",checks,"cases",results,"actual_native_or_explicit_test_reflection_changes",nativeChanges,"error",error==null?null:error.toString(),"scope","Off-executor final publication validation with actual native graph/setter/cache changes, raw field/collection/transform snapshots and property listeners; synthetic original fault, successful wrapper and exact host selection; explicit test reflection invalidates otherwise private cache/root fields","native_validation_writes_observed",error==null?0:null,"actual_native_job_initializations",0,"actual_native_feeds",0,"actual_native_job_placements",0,"bridge_continuation_qualified",false,"restart_reattachment_qualified",false,"hardware_qualified",false,"public_package_qualified",false);Files.writeString(root.resolve("proof.json"),JSON.toJson(proof)+"\n");System.out.println("NATIVE_REPLACEMENT_CONTINUATION_READ_ONLY_RESULT "+JSON.toJson(proof));}
        System.exit(error==null?0:1);
    }
}
