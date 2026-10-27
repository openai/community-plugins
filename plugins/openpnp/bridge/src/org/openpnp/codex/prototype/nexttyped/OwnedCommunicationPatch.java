/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex.prototype.nexttyped;

import com.google.gson.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import org.openpnp.codex.prototype.TypedGcodeProfile;
import org.openpnp.codex.prototype.OwnedProtocolSession;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.machine.reference.driver.GcodeDriver;
import org.openpnp.model.Configuration;
import org.openpnp.spi.Axis;
import org.openpnp.spi.MotionPlanner.CompletionType;

/** Private experiment. An exact newly materialized profile, never an existing/GUI driver.
 * No endpoint, template, property, serial or raw command is accepted by the typed patch.
 * Caller must retain exclusive native-model ownership; durable process recovery is absent. */
public final class OwnedCommunicationPatch implements AutoCloseable {
    public static final String TYPE="set_owned_gcode_timing";
    private final Configuration config;
    private final TypedGcodeProfile.Bundle bundle;
    private final Thread owner;
    private final LinkedHashMap<String,Receipt> receipts=new LinkedHashMap<>();
    private final ReferenceHead head;
    private String baseline;
    private long revision;
    private String lifecycle="draft";
    private boolean firstConnectAttempted,unresolved,nativeDisconnectCalled;
    private OwnedProtocolSession session;

    public static final class Fault extends IllegalArgumentException {
        public final String code;
        Fault(String code,String message){super(message);this.code=code;}
    }
    private static Fault fail(String code,String message){return new Fault(code,message);}
    /** Factory owns creation so no pre-existing connection history is asserted away. */
    public static OwnedCommunicationPatch create(Configuration config,TypedGcodeProfile.Spec initial,TypedGcodeProfile.OwnedEndpoint endpoint)throws Exception {
        if(config==null||Configuration.get()!=config||config.getMachine()!=null)throw fail("FRESH_CONFIGURATION_REQUIRED","Use the current new empty native configuration.");
        if(initial==null)throw fail("INVALID_PROFILE","A fully validated fixed profile is required.");
        if(config.getSystemUnits()!=org.openpnp.model.LengthUnit.Millimeters)throw fail("PROFILE_DRIFT","Owned native configuration units must be millimeters before model creation.");
        TypedGcodeProfile.Bundle bundle=initial.materialize(endpoint);
        config.setMachine(bundle.machine());
        return new OwnedCommunicationPatch(config,bundle);
    }
    private OwnedCommunicationPatch(Configuration config,TypedGcodeProfile.Bundle bundle){
        this.config=config;this.bundle=bundle;owner=Thread.currentThread();head=(ReferenceHead)bundle.machine().getHeads().get(0);
        binding();baseline=capture().toString();
    }
    private void thread(){if(Thread.currentThread()!=owner)throw fail("WRONG_OWNER_THREAD","The creating native owner thread must perform every operation.");}
    private void binding(){
        thread();bundle.endpoint().check();
        if(Configuration.get()!=config||config.getMachine()!=bundle.machine()||bundle.machine().getClass()!=ReferenceMachine.class||bundle.driver().getClass()!=GcodeDriver.class||bundle.nozzle().getClass()!=ReferenceNozzle.class)
            throw fail("NATIVE_IDENTITY_CHANGED","Native configuration and exact owned model identities must match.");
        if(bundle.machine().getDrivers().size()!=1||bundle.machine().getDrivers().get(0)!=bundle.driver()||bundle.machine().getAxes().size()!=4||bundle.machine().getHeads().size()!=1||bundle.machine().getHeads().get(0)!=head||head.getNozzles().size()!=1||head.getNozzles().get(0)!=bundle.nozzle()||!bundle.machine().getCameras().isEmpty()||!bundle.machine().getFeeders().isEmpty()||!bundle.machine().getActuators().isEmpty()||!head.getCameras().isEmpty()||!head.getActuators().isEmpty())
            throw fail("NATIVE_GRAPH_CHANGED","Only the exact owned fixture graph is supported.");
        Set<Axis.Type> types=EnumSet.noneOf(Axis.Type.class);
        for(int i=0;i<4;i++){
            ReferenceControllerAxis axis=bundle.axes().get(i);
            if(axis.getClass()!=ReferenceControllerAxis.class||bundle.machine().getAxes().get(i)!=axis||axis.getDriver()!=bundle.driver()||!types.add(axis.getType())||bundle.nozzle().getAxis(axis.getType())!=axis)
                throw fail("NATIVE_GRAPH_CHANGED","Owned axis identities and nozzle mappings must match.");
        }
        try{TypedGcodeProfile.requireFixedProtocol(bundle);}catch(IllegalArgumentException error){throw fail("PROFILE_DRIFT","Owned endpoint or fixed native protocol changed.");}
        // Also check the full public command inventory: head-specific/duplicate
        // entries are invisible to a default-command-only comparison.
        if(bundle.driver().commands.size()!=TypedGcodeProfile.COMMANDS.size())throw fail("PROFILE_DRIFT","Unexpected native command inventory.");
        Set<GcodeDriver.CommandType> seen=EnumSet.noneOf(GcodeDriver.CommandType.class);
        for(GcodeDriver.Command command:bundle.driver().commands)
            if(command==null||command.headMountableId!=null||command.type==null||!seen.add(command.type)||!Objects.equals(TypedGcodeProfile.COMMANDS.get(command.type),command.getCommand()))
                throw fail("PROFILE_DRIFT","Only the exact default fixed command inventory is supported.");
    }
    private JsonObject capture(){
        JsonObject snapshot=TypedGcodeProfile.describe(bundle);
        JsonObject mappings=new JsonObject();for(ReferenceControllerAxis a:bundle.axes())mappings.addProperty(a.getType().name(),bundle.nozzle().getAxis(a.getType()).getId());
        snapshot.add("nozzle_axis_ids",mappings);snapshot.addProperty("head_id",head.getId());snapshot.addProperty("nozzle_id",bundle.nozzle().getId());
        return snapshot;
    }
    private void unchanged(){binding();if(!capture().toString().equals(baseline))throw fail("NATIVE_BASELINE_CHANGED","Native settings differ from the immutable owned baseline.");}
    private void draft(){
        thread();if(firstConnectAttempted||!"draft".equals(lifecycle))throw fail("LIFECYCLE_NOT_DRAFT","Timing changes require the fresh pre-first-connect lifecycle.");
        unchanged();if(bundle.machine().isEnabled()||bundle.machine().isHomed()||bundle.machine().isBusy()||bundle.driver().isMotionPending())throw fail("NATIVE_STATE_NOT_QUIESCENT","Owned fixture must be disabled, unhomed, idle and without pending native motion.");
    }
    private String revision(){return "comm-"+revision;}
    public JsonObject snapshot(){
        thread();JsonObject out=new JsonObject();out.addProperty("profile",TypedGcodeProfile.PROFILE);out.addProperty("driver_id",bundle.driver().getId());
        out.addProperty("revision",revision());out.addProperty("baseline_sha256",digest(baseline));out.addProperty("lifecycle",lifecycle);
        out.addProperty("first_connect_attempted",firstConnectAttempted);out.addProperty("unresolved",unresolved);out.addProperty("native_disconnect_called",nativeDisconnectCalled);out.addProperty("transport_status","not-independently-observed");
        out.addProperty("native_motion_pending",bundle.driver().isMotionPending());out.addProperty("physical_standstill_verified",false);out.addProperty("hardware_qualified",false);
        out.add("baseline",new JsonParser().parse(baseline));return out;
    }
    public static final class Plan {
        private final OwnedCommunicationPatch owner;private final long revision;private final String baseline;
        private final int timeout,wait;
        private Plan(OwnedCommunicationPatch owner,long revision,String baseline,int timeout,int wait){this.owner=owner;this.revision=revision;this.baseline=baseline;this.timeout=timeout;this.wait=wait;}
        public JsonObject preview(){JsonObject out=new JsonObject();out.addProperty("type",TYPE);out.addProperty("expected_revision","comm-"+revision);out.addProperty("source_sha256",digest(baseline));
            out.addProperty("command_timeout_ms",timeout);out.addProperty("connect_wait_ms",wait);out.addProperty("network_effects",false);out.addProperty("geometry_changed",false);out.addProperty("physical_qualification",false);return out;}
    }
    public Plan stage(JsonObject change){
        exact(change,"type","profile","driver_id","expected_revision","expected_fingerprint","command_timeout_ms","connect_wait_ms");
        String type=string(change,"type"),profile=string(change,"profile"),driver=string(change,"driver_id"),rev=string(change,"expected_revision"),fingerprint=string(change,"expected_fingerprint");
        int timeout=integer(change,"command_timeout_ms",100,2000),wait=integer(change,"connect_wait_ms",0,100);
        if(!TYPE.equals(type)||!TypedGcodeProfile.PROFILE.equals(profile))throw fail("UNSUPPORTED_PROFILE","Only owned-loopback-gcode-v1 timing settings are supported.");
        draft();if(!bundle.driver().getId().equals(driver)||!revision().equals(rev)||!digest(baseline).equals(fingerprint))throw fail("STALE_PATCH","Driver, revision and baseline digest must match.");
        return new Plan(this,revision,baseline,timeout,wait);
    }
    public JsonObject apply(Plan plan){
        if(plan==null||plan.owner!=this)throw fail("PLAN_OWNER_MISMATCH","Patch belongs to another owned context.");
        draft();if(plan.revision!=revision||!plan.baseline.equals(baseline))throw fail("STALE_PATCH","Native baseline or revision changed after preview.");
        boolean changed=bundle.driver().getTimeoutMilliseconds()!=plan.timeout||bundle.driver().getConnectWaitTimeMilliseconds()!=plan.wait;
        if(changed){
            // Pinned exact-class setters assign primitive fields, without I/O/listeners.
            // Any unexpected failure remains fenced; do not claim automatic rollback.
            revision++;
            try{bundle.driver().setTimeoutMilliseconds(plan.timeout);bundle.driver().setConnectWaitTimeMilliseconds(plan.wait);binding();baseline=capture().toString();}
            catch(Throwable error){lifecycle="configuration_fault";throw fail("CONFIGURATION_FAULT","Unexpected failure while applying owned native timing settings.");}
        }
        JsonObject result=snapshot();result.addProperty("changed",changed);result.addProperty("network_effects",false);return result;
    }
    private static final class Receipt {final String kind,json;Receipt(String kind,JsonObject result){this.kind=kind;json=result.toString();}}
    private JsonObject existing(String request,String kind){
        thread();if(request==null||!request.matches("[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"))throw fail("INVALID_REQUEST_ID","Use a lowercase request UUID.");
        Receipt found=receipts.get(request);if(found!=null){if(!found.kind.equals(kind))throw fail("REQUEST_ID_CONFLICT","Request already refers to a different operation.");return copy(found.json);}
        if(receipts.size()>=64)throw fail("REQUEST_CAPACITY","Owned prototype retains at most64 request receipts.");return null;
    }
    private JsonObject receipt(String request,String kind){JsonObject out=new JsonObject();out.addProperty("request_id",request);out.addProperty("kind",kind);out.addProperty("authority","owned-loopback-protocol-test");out.addProperty("physical_qualification",false);out.addProperty("automatic_replay",false);out.addProperty("physical_standstill_verified",false);return out;}
    private JsonObject remember(String request,String kind,JsonObject value){receipts.put(request,new Receipt(kind,value));return copy(value.toString());}
    public JsonObject connectOnce(String request){
        JsonObject prior=existing(request,"connect");if(prior!=null)return prior;draft();firstConnectAttempted=true;lifecycle="connecting";
        JsonObject out=receipt(request,"connect");
        try{session=new OwnedProtocolSession(bundle);session.connectOnce();lifecycle="connected";out.addProperty("state","native_connect_returned");}
        catch(Exception error){unresolved=true;lifecycle="outcome_unknown";out.addProperty("state","outcome_unknown");out.addProperty("native_error_type",error.getClass().getName());bundle.driver().disconnect();nativeDisconnectCalled=true;}
        out.addProperty("native_motion_pending",bundle.driver().isMotionPending());return remember(request,"connect",out);
    }
    private void connected(){thread();if(!"connected".equals(lifecycle)||unresolved)throw fail("SESSION_FENCED","Owned session cannot send new requests.");unchanged();}
    public JsonObject identifyOnce(String request){
        JsonObject prior=existing(request,"identify");if(prior!=null)return prior;connected();JsonObject out=session.identifyOnce(request);
        if("outcome_unknown".equals(out.get("state").getAsString())){unresolved=true;lifecycle="outcome_unknown";nativeDisconnectCalled=true;}
        out.addProperty("native_motion_pending",bundle.driver().isMotionPending());return remember(request,"identify",out);
    }
    /** Only a native stillstand/completion observation, never an emergency-stop API. */
    public JsonObject observeCompletionOnce(String request){
        JsonObject prior=existing(request,"completion");if(prior!=null)return prior;connected();JsonObject out=receipt(request,"completion");
        try{bundle.driver().waitForCompletion(bundle.nozzle(),CompletionType.WaitForStillstand);bundle.driver().receiveResponses();out.addProperty("state","native_completion_returned");}
        catch(Exception error){unresolved=true;lifecycle="outcome_unknown";out.addProperty("state","outcome_unknown");out.addProperty("native_error_type",error.getClass().getName());}
        out.addProperty("native_motion_pending",bundle.driver().isMotionPending());return remember(request,"completion",out);
    }
    // Package-private access for native-model qualification only; no public raw-driver operation.
    TypedGcodeProfile.Bundle testBundle(){return bundle;}
    @Override public void close(){thread();if(session!=null)session.close();else bundle.driver().disconnect();nativeDisconnectCalled=true;if(!unresolved&&!"configuration_fault".equals(lifecycle))lifecycle="closed";}
    private static JsonObject copy(String json){return new JsonParser().parse(json).getAsJsonObject();}
    private static void exact(JsonObject json,String... wanted){if(json==null||!json.entrySet().stream().map(Map.Entry::getKey).collect(java.util.stream.Collectors.toSet()).equals(new HashSet<>(Arrays.asList(wanted))))throw fail("INVALID_FIELDS","Require exactly the documented timing patch fields.");}
    private static String string(JsonObject json,String key){JsonElement value=json.get(key);if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isString()||value.getAsString().length()>128)throw fail("INVALID_ARGUMENT","Expected bounded typed string.");return value.getAsString();}
    private static int integer(JsonObject json,String key,int low,int high){JsonElement value=json.get(key);if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isNumber())throw fail("INVALID_ARGUMENT","Expected integral timing value.");int result;try{result=value.getAsBigDecimal().intValueExact();}catch(RuntimeException error){throw fail("INVALID_ARGUMENT","Timing value must be exactly integral.");}if(result<low||result>high)throw fail("OUT_OF_RANGE","Timing value is outside profile bounds.");return result;}
    private static String digest(String text){try{byte[] bytes=MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8));StringBuilder out=new StringBuilder();for(byte b:bytes)out.append(String.format("%02x",b));return out.toString();}catch(Exception error){throw new IllegalStateException(error);}}
}
