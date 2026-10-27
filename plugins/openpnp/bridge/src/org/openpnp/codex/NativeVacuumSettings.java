/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.util.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.ReferenceNozzleTip.VacuumMeasurementMethod;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.model.Configuration;
import org.openpnp.spi.*;

/** Typed native settings only. The caller owns revision, journal, save and source admission.
 * No sampling, valve action, occupancy reconciliation or source authority is performed here. */
public final class NativeVacuumSettings {
    public static final String TYPE="set_vacuum_sensing_settings";
    public static final String UNITS="native-actuator-units";
    public static final String PROVENANCE="configured-thresholds";
    public static final Set<String> FIELDS=Collections.unmodifiableSet(new LinkedHashSet<>(Arrays.asList(
        "type","nozzle_id","nozzle_tip_id","vacuum_sense_actuator_id","vacuum_actuator_id","reading_units","threshold_provenance",
        "method_part_on","method_part_off","part_on_low","part_on_high","part_off_low","part_off_high",
        "part_on_check_after_pick","part_on_check_align","part_on_check_before_place","part_off_check_after_place","part_off_check_before_pick",
        "part_off_probe_ms","part_off_dwell_ms")));
    private NativeVacuumSettings() { }

    public static final class Plan {
        private final Configuration config;
        private final Machine machine;
        private final ReferenceNozzle nozzle;
        private final ReferenceNozzleTip tip;
        private final ReferenceActuator sense,valve;
        private final Binding binding;
        private final Map<String,Object> baseline,desired;
        private boolean used;
        private Plan(Configuration c,ReferenceNozzle n,ReferenceNozzleTip t,ReferenceActuator s,ReferenceActuator v,Map<String,Object> values)throws Exception {
            config=c;machine=c.getMachine();nozzle=n;tip=t;sense=s;valve=v;
            binding=bind(c,n,t,s,v);baseline=settings(n,t);desired=Collections.unmodifiableMap(new LinkedHashMap<>(values));
            quiescent(machine);
        }
        public void validateCurrentState()throws Exception {
            if(used)fail("PATCH_ALREADY_APPLIED","Vacuum settings plan cannot be replayed");
            Binding now=bind(config,nozzle,tip,sense,valve);
            if(!binding.same(now)||!baseline.equals(settings(nozzle,tip)))fail("STALE_VACUUM_PLAN","Native identities, consumers, bindings or sensing settings changed after staging");
            quiescent(machine);
        }
        public void apply()throws Exception {
            if(!machine.isTask(Thread.currentThread()))fail("NATIVE_EXECUTOR_REQUIRED","Vacuum settings require the native executor");
            validateCurrentState();used=true;
            // All fields are preflighted before the first setter. These exact pinned setters
            // assign primitives/references. Any unexpected failure consumes the plan; the
            // caller's configuration-fault fence must retain partial state, never retry it.
            try {
                nozzle.setVacuumSenseActuator(sense);
                tip.setMethodPartOn(method(desired,"method_part_on"));
                tip.setMethodPartOff(method(desired,"method_part_off"));
                tip.setVacuumLevelPartOnLow(value(desired,"part_on_low"));tip.setVacuumLevelPartOnHigh(value(desired,"part_on_high"));
                tip.setVacuumLevelPartOffLow(value(desired,"part_off_low"));tip.setVacuumLevelPartOffHigh(value(desired,"part_off_high"));
                tip.setPartOnCheckAfterPick(flag(desired,"part_on_check_after_pick"));tip.setPartOnCheckAlign(flag(desired,"part_on_check_align"));tip.setPartOnCheckBeforePlace(flag(desired,"part_on_check_before_place"));
                tip.setPartOffCheckAfterPlace(flag(desired,"part_off_check_after_place"));tip.setPartOffCheckBeforePick(flag(desired,"part_off_check_before_pick"));
                tip.setPartOffProbingMilliseconds(((Number)desired.get("part_off_probe_ms")).intValue());tip.setPartOffDwellMilliseconds(((Number)desired.get("part_off_dwell_ms")).intValue());
                Binding after=bind(config,nozzle,tip,sense,valve);
                if(!binding.sameExceptSense(after,nozzle,sense)||!desired.equals(settings(nozzle,tip)))fail("CONFIGURATION_FAULT","Native readback differs from the admitted vacuum settings");
            } catch(Exception failure) {
                if(failure instanceof Bridge.Fault&&"CONFIGURATION_FAULT".equals(((Bridge.Fault)failure).code))throw failure;
                throw new Bridge.Fault("CONFIGURATION_FAULT","Native vacuum settings changed without complete verified readback");
            }
        }
        public Map<String,Object> metadata(){return map("type",TYPE,"nozzle_id",nozzle.getId(),"nozzle_tip_id",tip.getId(),"before",immutable(baseline),"after",immutable(desired),
            "affected_nozzle_ids",List.copyOf(binding.affected),"tip_settings_shared",binding.affected.size()>1,"requires_disabled",true,"requires_native_executor",true,
            "establish_part_on_level",false,"establish_part_off_level",false,"measurement_performed",false,"signal_provenance","not-attested-by-settings-adapter",
            "source_authority_granted",false,"occupancy_reconciled",false,"physical_qualification",false,"automatic_rollback",false,
            "caller_must_invalidate",List.of("job-validation","vacuum-check-readiness"),"caller_must_preserve",List.of("native-held-part-state","durable-occupancy-history","source-provenance"));}
    }

    public static Plan stage(Configuration c,JsonObject change)throws Exception {
        if(change==null||!change.entrySet().stream().map(Map.Entry::getKey).collect(java.util.stream.Collectors.toSet()).equals(FIELDS))fail("INVALID_ARGUMENT","Require exactly the documented vacuum settings fields");
        if(!TYPE.equals(text(change,"type"))||!UNITS.equals(text(change,"reading_units"))||!PROVENANCE.equals(text(change,"threshold_provenance")))fail("INVALID_ARGUMENT","Vacuum thresholds require explicit native actuator units and settings provenance");
        Machine m=machine(c);String nozzleId=id(change,"nozzle_id"),tipId=id(change,"nozzle_tip_id"),valveId=id(change,"vacuum_actuator_id");
        ReferenceNozzle n=findNozzle(m,nozzleId);NozzleTip found=m.getNozzleTip(tipId);
        if(found==null||found.getClass()!=ReferenceNozzleTip.class)fail("INVALID_VACUUM_BINDING","Target tip must be an existing exact ReferenceNozzleTip");
        ReferenceNozzleTip tip=(ReferenceNozzleTip)found;ReferenceActuator valve=findActuator(n.getHead(),valveId);
        JsonElement raw=change.get("vacuum_sense_actuator_id");ReferenceActuator sense=raw.isJsonNull()?null:findActuator(n.getHead(),id(change,"vacuum_sense_actuator_id"));
        Map<String,Object> next=map("type",TYPE,"nozzle_id",nozzleId,"nozzle_tip_id",tipId,"vacuum_sense_actuator_id",sense==null?null:sense.getId(),"vacuum_actuator_id",valveId,
            "reading_units",UNITS,"threshold_provenance",PROVENANCE,"method_part_on",methodText(change,"method_part_on"),"method_part_off",methodText(change,"method_part_off"),
            "part_on_low",number(change,"part_on_low"),"part_on_high",number(change,"part_on_high"),"part_off_low",number(change,"part_off_low"),"part_off_high",number(change,"part_off_high"),
            "part_on_check_after_pick",bool(change,"part_on_check_after_pick"),"part_on_check_align",bool(change,"part_on_check_align"),"part_on_check_before_place",bool(change,"part_on_check_before_place"),
            "part_off_check_after_place",bool(change,"part_off_check_after_place"),"part_off_check_before_pick",bool(change,"part_off_check_before_pick"),
            "part_off_probe_ms",integer(change,"part_off_probe_ms"),"part_off_dwell_ms",integer(change,"part_off_dwell_ms"));
        ranges(next);if(sense==null&&(!"None".equals(next.get("method_part_on"))||!"None".equals(next.get("method_part_off"))))fail("INVALID_VACUUM_BINDING","Enabled sensing needs an exact existing same-head sense actuator");
        return new Plan(c,n,tip,sense,valve,next);
    }

    /** Safe configuration readback; unsupported native data is never labelled editable. */
    public static Map<String,Object> describe(Configuration c,ReferenceNozzle n) {
        NozzleTip t=n.getNozzleTip();
        if(t==null||t.getClass()!=ReferenceNozzleTip.class)return map("settings_editable",false,"code","NO_INSTALLED_EXACT_TIP","nozzle_id",n.getId(),"physical_qualification",false);
        try{return describe(c,n,(ReferenceNozzleTip)t);}catch(Exception e){return map("settings_editable",false,"code",e instanceof Bridge.Fault?((Bridge.Fault)e).code:"UNSUPPORTED_NATIVE_SENSING","nozzle_id",n.getId(),"nozzle_tip_id",t.getId(),"physical_qualification",false);}
    }
    public static Map<String,Object> describe(Configuration c,ReferenceNozzle n,ReferenceNozzleTip t)throws Exception {
        ReferenceActuator valve=exactActuator(n.getVacuumActuator()),sense=n.getVacuumSenseActuator()==null?null:exactActuator(n.getVacuumSenseActuator());
        Binding b=bind(c,n,t,sense,valve);Map<String,Object> out=new LinkedHashMap<>(settings(n,t));ranges(out);
        out.putAll(map("settings_editable",true,"affected_nozzle_ids",List.copyOf(b.affected),"tip_settings_shared",b.affected.size()>1,"establish_part_on_level",false,"establish_part_off_level",false,
            "nozzle_pick_dwell_ms",n.getPickDwellMilliseconds(),"nozzle_place_dwell_ms",n.getPlaceDwellMilliseconds(),"tip_pick_dwell_ms",t.getPickDwellMilliseconds(),"tip_place_dwell_ms",t.getPlaceDwellMilliseconds(),
            "signal_provenance","not-attested-by-settings-adapter","source_authority_granted",false,"measurement_performed",false,"physical_qualification",false));return immutable(out);
    }
    /** Settings-only descriptor. A snapshot caller must separately handle shared-tip restore
     * ordering and explicitly exclude source authority and occupancy from restoration. */
    public static JsonObject changeForSnapshot(Configuration c,ReferenceNozzle n,ReferenceNozzleTip t)throws Exception {
        describe(c,n,t);return new GsonBuilder().serializeNulls().create().toJsonTree(settings(n,t)).getAsJsonObject();
    }

    private static final class Binding {
        final List<Object> objects=new ArrayList<>();final List<Object> values=new ArrayList<>();final List<String> affected=new ArrayList<>();
        final Map<ReferenceNozzle,Integer> senseSlots=new IdentityHashMap<>();
        boolean same(Binding b){return values.equals(b.values)&&sameObjects(objects,b.objects);}
        boolean sameExceptSense(Binding b,ReferenceNozzle n,ReferenceActuator sense){List<Object> expected=new ArrayList<>(objects);expected.set(senseSlots.get(n),sense);return values.equals(b.values)&&sameObjects(expected,b.objects);}
    }
    private static Binding bind(Configuration c,ReferenceNozzle n,ReferenceNozzleTip t,ReferenceActuator sense,ReferenceActuator valve)throws Exception {
        Machine m=machine(c);if(n==null||n.getClass()!=ReferenceNozzle.class||t==null||t.getClass()!=ReferenceNozzleTip.class||m.getNozzleTip(t.getId())!=t||!n.getCompatibleNozzleTips().contains(t))fail("INVALID_VACUUM_BINDING","Require existing exact compatible nozzle and canonical tip");
        if(n.getVacuumActuator()!=valve||valve==null)fail("INVALID_VACUUM_BINDING","The existing bound vacuum valve is observed, never replaced by this change");
        if(m.getHeads().size()>8||m.getNozzleTips().size()>128)fail("VACUUM_GRAPH_LIMIT","Native sensing graph exceeds the bounded profile");
        Binding b=new Binding();Collections.addAll(b.objects,c,m,m.getDrivers().get(0),m.getMotionPlanner());Set<String> tipIds=new HashSet<>(),nozzleIds=new HashSet<>(),actuatorIds=new HashSet<>();
        for(NozzleTip each:m.getNozzleTips()){if(each==null||!tipIds.add(nativeId(each.getId())))fail("INVALID_VACUUM_BINDING","Duplicate or invalid native tip identity");b.objects.add(each);b.values.add(each.getId());}
        boolean target=false;int nozzles=0,actuators=0;
        for(Head h:m.getHeads()) {
            if(h==null||h.getClass()!=ReferenceHead.class)fail("INVALID_VACUUM_BINDING","Only exact ReferenceHead consumers are supported");b.objects.add(h);b.values.add(h.getId());Set<String> names=new HashSet<>();
            for(Actuator a:h.getActuators()) {
                if(++actuators>64||a==null||!actuatorIds.add(nativeId(a.getId())))fail("INVALID_VACUUM_BINDING","Duplicate actuator identity or excessive graph");
                if(a.getName()==null||a.getName().isEmpty()||a.getName().length()>256||!names.add(a.getName().toLowerCase(Locale.ROOT))||h.getActuatorByName(a.getName())!=a)fail("INVALID_VACUUM_BINDING","Actuator names must roundtrip uniquely in their native head");
                b.objects.add(a);b.values.add(a.getId());b.values.add(a.getName());
                {ReferenceActuator r=exactActuator(a);if(r.getHead()!=h||r.getDriver()!=m.getDrivers().get(0))fail("INVALID_VACUUM_BINDING","Sensing and valve use this head and the exact installed simulator driver");
                    b.objects.add(r.getDriver());Collections.addAll(b.values,r.getIndex(),r.getValueType(),r.getDefaultOnDouble(),r.getDefaultOffDouble(),r.getDefaultOnString(),r.getDefaultOffString(),r.getCoordinatedBeforeActuateEnum(),r.getCoordinatedAfterActuateEnum(),r.getCoordinatedBeforeReadEnum());}
            }
            for(Nozzle each:h.getNozzles()) {
                if(++nozzles>32||each==null||each.getClass()!=ReferenceNozzle.class||!nozzleIds.add(nativeId(each.getId()))||each.getHead()!=h)fail("INVALID_VACUUM_BINDING","Require bounded unique exact native nozzle consumers");
                ReferenceNozzle r=(ReferenceNozzle)each;b.objects.add(r);b.values.add(r.getId());b.objects.add(r.getNozzleTip());b.objects.add(r.getVacuumActuator());b.senseSlots.put(r,b.objects.size());b.objects.add(r.getVacuumSenseActuator());
                List<NozzleTip> compatible=new ArrayList<>(r.getCompatibleNozzleTips());compatible.sort(Comparator.comparing(NozzleTip::getId));for(NozzleTip k:compatible){if(m.getNozzleTip(k.getId())!=k)fail("INVALID_VACUUM_BINDING","Nozzle compatibility must reference exact canonical tips");b.objects.add(k);b.values.add(k.getId());}
                if(compatible.contains(t)){b.affected.add(r.getId());dwell(r.getPickDwellMilliseconds());dwell(r.getPlaceDwellMilliseconds());if((long)r.getPickDwellMilliseconds()+t.getPickDwellMilliseconds()>2000||(long)r.getPlaceDwellMilliseconds()+t.getPlaceDwellMilliseconds()>2000)fail("UNBOUNDED_VACUUM_DWELL","Effective nozzle/tip dwell exceeds 2000ms");Collections.addAll(b.values,r.getPickDwellMilliseconds(),r.getPlaceDwellMilliseconds());}
                if(r==n)target=true;
            }
        }
        if(!target||n.getHead().getActuators().stream().noneMatch(a->a==valve)||(sense!=null&&n.getHead().getActuators().stream().noneMatch(a->a==sense)))fail("INVALID_VACUUM_BINDING","Sensing must bind existing actuators in the selected nozzle head");
        if(n.getVacuumSenseActuator()!=null&&n.getHead().getActuators().stream().noneMatch(a->a==n.getVacuumSenseActuator()))fail("INVALID_VACUUM_BINDING","Existing sense binding must be an installed same-head actuator");
        if(t.isEstablishPartOnLevel()||t.isEstablishPartOffLevel())fail("UNSUPPORTED_VACUUM_METHOD","Continuous establishment graphs are outside this settings profile");
        dwell(t.getPickDwellMilliseconds());dwell(t.getPlaceDwellMilliseconds());dwell(t.getPartOffProbingMilliseconds());dwell(t.getPartOffDwellMilliseconds());
        if(requireMethod(t,true)==VacuumMeasurementMethod.Difference||requireMethod(t,false)==VacuumMeasurementMethod.Difference)fail("UNSUPPORTED_VACUUM_METHOD","Difference sensing is outside the Absolute profile");
        ranges(settings(n,t));
        Collections.addAll(b.values,t.getPickDwellMilliseconds(),t.getPlaceDwellMilliseconds(),t.isEstablishPartOnLevel(),t.isEstablishPartOffLevel());
        return b;
    }
    private static Machine machine(Configuration c)throws Exception {if(c==null||Configuration.get()!=c||c.getMachine()==null||c.getMachine().getClass()!=ReferenceMachine.class||c.getMachine().getDrivers().size()!=1||c.getMachine().getDrivers().get(0).getClass()!=NullDriver.class)fail("UNSUPPORTED_SIMULATOR","Vacuum settings require the current exact ReferenceMachine and one exact NullDriver");return c.getMachine();}
    private static void quiescent(Machine m)throws Exception {if(m.isEnabled()||(m.isBusy()&&!m.isTask(Thread.currentThread())))fail("MACHINE_NOT_QUIESCENT","Disable the simulator and wait for native work before sensing edits");for(Head h:m.getHeads())for(Nozzle n:h.getNozzles())if(n.getPart()!=null)fail("HELD_PART","Held native parts block sensing configuration; settings never reconcile occupancy");}
    private static ReferenceNozzle findNozzle(Machine m,String id)throws Exception {ReferenceNozzle found=null;for(Head h:m.getHeads())for(Nozzle n:h.getNozzles())if(id.equals(n.getId())){if(found!=null||n.getClass()!=ReferenceNozzle.class)fail("INVALID_VACUUM_BINDING","Nozzle identity must be unique and exact");found=(ReferenceNozzle)n;}if(found==null)fail("INVALID_VACUUM_BINDING","Nozzle must exist");return found;}
    private static ReferenceActuator findActuator(Head h,String id)throws Exception {if(h==null)fail("INVALID_VACUUM_BINDING","Nozzle head is absent");ReferenceActuator found=null;for(Actuator a:h.getActuators())if(id.equals(a.getId())){if(found!=null)fail("INVALID_VACUUM_BINDING","Actuator identity is ambiguous");found=exactActuator(a);}if(found==null)fail("INVALID_VACUUM_BINDING","Actuator must already exist in this head");return found;}
    private static ReferenceActuator exactActuator(Actuator a)throws Exception {if(a==null||a.getClass()!=ReferenceActuator.class)fail("INVALID_VACUUM_BINDING","Require an exact ReferenceActuator binding");return (ReferenceActuator)a;}
    private static Map<String,Object> settings(ReferenceNozzle n,ReferenceNozzleTip t)throws Exception {return map("type",TYPE,"nozzle_id",n.getId(),"nozzle_tip_id",t.getId(),"vacuum_sense_actuator_id",n.getVacuumSenseActuator()==null?null:n.getVacuumSenseActuator().getId(),"vacuum_actuator_id",n.getVacuumActuator()==null?null:n.getVacuumActuator().getId(),"reading_units",UNITS,"threshold_provenance",PROVENANCE,
        "method_part_on",requireMethod(t,true).name(),"method_part_off",requireMethod(t,false).name(),"part_on_low",t.getVacuumLevelPartOnLow(),"part_on_high",t.getVacuumLevelPartOnHigh(),"part_off_low",t.getVacuumLevelPartOffLow(),"part_off_high",t.getVacuumLevelPartOffHigh(),
        "part_on_check_after_pick",t.isPartOnCheckAfterPick(),"part_on_check_align",t.isPartOnCheckAlign(),"part_on_check_before_place",t.isPartOnCheckBeforePlace(),"part_off_check_after_place",t.isPartOffCheckAfterPlace(),"part_off_check_before_pick",t.isPartOffCheckBeforePick(),"part_off_probe_ms",t.getPartOffProbingMilliseconds(),"part_off_dwell_ms",t.getPartOffDwellMilliseconds());}
    /** Fixed pinned-field read for sensing settings/source/transfer guards. Public native
     * getters normalize null to None; raw null must remain unknown without a model mutation.
     * The caller selects its own refusal code for an absent method. */
    static VacuumMeasurementMethod readMethod(ReferenceNozzleTip tip,boolean partOn)throws Exception {
        if(tip==null||tip.getClass()!=ReferenceNozzleTip.class)fail("INVALID_VACUUM_BINDING","Require an exact native nozzle tip for method inspection");
        try {java.lang.reflect.Field field=ReferenceNozzleTip.class.getDeclaredField(partOn?"methodPartOn":"methodPartOff");field.setAccessible(true);Object value=field.get(tip);
            if(value!=null&&value.getClass()!=VacuumMeasurementMethod.class)fail("UNSUPPORTED_VACUUM_METHOD","Unknown pinned native sensing method");
            return (VacuumMeasurementMethod)value;
        }catch(ReflectiveOperationException|SecurityException unsupported){fail("UNSUPPORTED_VACUUM_METHOD","Pinned native sensing method cannot be inspected without normalization");return null;}
    }
    private static VacuumMeasurementMethod requireMethod(ReferenceNozzleTip tip,boolean partOn)throws Exception {
        VacuumMeasurementMethod method=readMethod(tip,partOn);if(method==null)fail("UNSUPPORTED_VACUUM_METHOD","An unset native sensing method is unknown; it cannot be normalized by settings admission");return method;
    }
    private static void ranges(Map<String,Object> m)throws Exception {double ol=value(m,"part_on_low"),oh=value(m,"part_on_high"),fl=value(m,"part_off_low"),fh=value(m,"part_off_high");for(double v:new double[]{ol,oh,fl,fh})if(!Double.isFinite(v)||Math.abs(v)>1e6)fail("OUT_OF_RANGE","Vacuum thresholds must be finite native actuator values within +/-1000000");
        boolean on="Absolute".equals(m.get("method_part_on")),off="Absolute".equals(m.get("method_part_off"));if(ol>oh||fl>fh||(on&&ol==oh)||(off&&fl==fh))fail("INVALID_VACUUM_RANGE","Absolute thresholds need strictly ordered intervals; None allows equal bounds");
        if(on&&off&&!(oh<fl||fh<ol))fail("OVERLAPPING_VACUUM_RANGES","Part-on and part-off intervals must be disjoint, including their boundaries");
        dwell(((Number)m.get("part_off_probe_ms")).intValue());dwell(((Number)m.get("part_off_dwell_ms")).intValue());}
    private static boolean sameObjects(List<Object>a,List<Object>b){if(a.size()!=b.size())return false;for(int i=0;i<a.size();i++)if(a.get(i)!=b.get(i))return false;return true;}
    private static String nativeId(String id)throws Exception {if(id==null||!id.matches("[A-Za-z0-9_.:+-]{1,128}"))fail("INVALID_VACUUM_BINDING","Native IDs must be bounded and unambiguous");return id.toLowerCase(Locale.ROOT);}
    private static String id(JsonObject c,String key)throws Exception {String value=text(c,key);nativeId(value);return value;}
    private static String text(JsonObject c,String key)throws Exception {JsonElement v=c.get(key);if(v==null||!v.isJsonPrimitive()||!v.getAsJsonPrimitive().isString())fail("INVALID_ARGUMENT","Expected string "+key);return v.getAsString();}
    private static String methodText(JsonObject c,String key)throws Exception {String s=text(c,key);if(!Set.of("None","Absolute").contains(s))fail("UNSUPPORTED_VACUUM_METHOD","Only None or Absolute sensing is supported");return s;}
    private static double number(JsonObject c,String key)throws Exception {JsonElement v=c.get(key);if(v==null||!v.isJsonPrimitive()||!v.getAsJsonPrimitive().isNumber())fail("INVALID_ARGUMENT","Expected number "+key);double n=v.getAsDouble();if(!Double.isFinite(n)||Math.abs(n)>1e6)fail("OUT_OF_RANGE","Threshold exceeds finite native-unit bounds");return n==0?0:n;}
    private static boolean bool(JsonObject c,String key)throws Exception {JsonElement v=c.get(key);if(v==null||!v.isJsonPrimitive()||!v.getAsJsonPrimitive().isBoolean())fail("INVALID_ARGUMENT","Expected boolean "+key);return v.getAsBoolean();}
    private static int integer(JsonObject c,String key)throws Exception {JsonElement v=c.get(key);if(v==null||!v.isJsonPrimitive()||!v.getAsJsonPrimitive().isNumber())fail("INVALID_ARGUMENT","Expected integral milliseconds");int n;try{n=v.getAsBigDecimal().intValueExact();}catch(ArithmeticException e){fail("INVALID_ARGUMENT","Milliseconds must be exactly integral");return 0;}dwell(n);return n;}
    private static void dwell(int n)throws Exception {if(n<0||n>1000)fail("UNBOUNDED_VACUUM_DWELL","Individual probe and dwell values must lie in 0..1000ms");}
    private static VacuumMeasurementMethod method(Map<String,Object>m,String k){return VacuumMeasurementMethod.valueOf((String)m.get(k));}
    private static double value(Map<String,Object>m,String k){return ((Number)m.get(k)).doubleValue();}
    private static boolean flag(Map<String,Object>m,String k){return (Boolean)m.get(k);}
    private static Map<String,Object> map(Object...v){Map<String,Object>m=new LinkedHashMap<>();for(int i=0;i<v.length;i+=2)m.put((String)v[i],v[i+1]);return m;}
    private static Map<String,Object> immutable(Map<String,Object>m){return Collections.unmodifiableMap(new LinkedHashMap<>(m));}
    private static void fail(String code,String message)throws Bridge.Fault {throw new Bridge.Fault(code,message);}
}
