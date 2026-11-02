/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex.prototype;

import com.google.gson.*;
import java.net.ServerSocket;
import java.util.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.machine.reference.driver.*;
import org.openpnp.machine.reference.driver.GcodeDriver.CommandType;
import org.openpnp.model.*;
import org.openpnp.spi.Axis;
import org.openpnp.spi.Driver.MotionControlType;

/** Offline prototype only. No production bridge dispatch or arbitrary controller endpoint. */
public final class TypedGcodeProfile {
    public static final String PROFILE="owned-loopback-gcode-v1";
    public static final String UPSTREAM="5bd404cfc70f34103a3ca0fbb6b50c2b465f407c";
    public static final Map<CommandType,String> COMMANDS;
    static {
        LinkedHashMap<CommandType,String> commands=new LinkedHashMap<>();
        commands.put(CommandType.COMMAND_CONFIRM_REGEX,"^ok$");
        commands.put(CommandType.COMMAND_ERROR_REGEX,"^error:[ -~]{1,160}$");
        commands.put(CommandType.CONNECT_COMMAND,"G21\nG90");
        commands.put(CommandType.MOVE_TO_COMMAND,"G0 {XL}{X:%.4f} {YL}{Y:%.4f} {ZL}{Z:%.4f} {RotationL}{Rotation:%.4f} {FeedRate:F%.1f}");
        commands.put(CommandType.MOVE_TO_COMPLETE_COMMAND,"M400");
        commands.put(CommandType.MOVE_TO_COMPLETE_REGEX,"^motion-complete$");
        COMMANDS=Collections.unmodifiableMap(commands);
    }
    public static final class Fault extends IllegalArgumentException {
        public final String code;
        Fault(String code,String message){super(message);this.code=code;}
    }
    private static Fault fail(String code,String message){return new Fault(code,message);}
    public static final class OwnedEndpoint {
        private final ServerSocket listener;
        private OwnedEndpoint(ServerSocket listener){this.listener=listener;check();}
        public int port(){check();return listener.getLocalPort();}
        public String host(){check();return "127.0.0.1";}
        public void check(){if(listener==null||listener.isClosed()||!listener.isBound()||!listener.getInetAddress().getHostAddress().equals("127.0.0.1"))throw fail("ENDPOINT_NOT_OWNED","Require this process's open listener bound to literal127.0.0.1.");}
    }
    /** Called only by a test that already owns the live listening socket. Never takes JSON/IP/port. */
    public static OwnedEndpoint ownedEndpoint(ServerSocket listener){return new OwnedEndpoint(listener);}
    /** Java11 equivalent of the former record: final components and value methods.
     * Preserve the public constructor's shallow/null behavior; stage() owns validation. */
    public static final class AxisSpec {
        private final Axis.Type type;
        private final String letter,units;
        private final double home,low,high,feed,acceleration,resolution;
        public AxisSpec(Axis.Type type,String letter,String units,double home,double low,double high,double feed,double acceleration,double resolution){
            this.type=type;this.letter=letter;this.units=units;this.home=home;this.low=low;this.high=high;this.feed=feed;this.acceleration=acceleration;this.resolution=resolution;
        }
        public Axis.Type type(){return type;}public String letter(){return letter;}public String units(){return units;}
        public double home(){return home;}public double low(){return low;}public double high(){return high;}
        public double feed(){return feed;}public double acceleration(){return acceleration;}public double resolution(){return resolution;}
        @Override public boolean equals(Object other){
            if(this==other)return true;if(!(other instanceof AxisSpec))return false;AxisSpec a=(AxisSpec)other;
            return Objects.equals(type,a.type)&&Objects.equals(letter,a.letter)&&Objects.equals(units,a.units)
                &&Double.compare(home,a.home)==0&&Double.compare(low,a.low)==0&&Double.compare(high,a.high)==0
                &&Double.compare(feed,a.feed)==0&&Double.compare(acceleration,a.acceleration)==0&&Double.compare(resolution,a.resolution)==0;
        }
        @Override public int hashCode(){
            int result=Objects.hashCode(type);result=31*result+Objects.hashCode(letter);result=31*result+Objects.hashCode(units);
            result=31*result+Double.hashCode(home);result=31*result+Double.hashCode(low);result=31*result+Double.hashCode(high);
            result=31*result+Double.hashCode(feed);result=31*result+Double.hashCode(acceleration);return 31*result+Double.hashCode(resolution);
        }
        @Override public String toString(){return "AxisSpec[type="+type+", letter="+letter+", units="+units+", home="+home+", low="+low+", high="+high+", feed="+feed+", acceleration="+acceleration+", resolution="+resolution+"]";}
    }
    public static final class Spec {
        private final String name; private final int timeout,connectWait,feedRate; private final List<AxisSpec> axes;
        private Spec(String name,int timeout,int connectWait,int feedRate,List<AxisSpec> axes){this.name=name;this.timeout=timeout;this.connectWait=connectWait;this.feedRate=feedRate;this.axes=List.copyOf(axes);}
        public Bundle materialize(OwnedEndpoint endpoint)throws Exception {
            endpoint.check();
            GcodeDriver driver=new GcodeDriver();driver.setName(name);
            driver.setUnits(LengthUnit.Millimeters);driver.setCommunicationsType(AbstractReferenceDriver.CommunicationsType.tcp);
            driver.getTcp().setIpAddress(endpoint.host());driver.getTcp().setPort(endpoint.port());
            driver.setLineEndingType(ReferenceDriverCommunications.LineEndingType.LF);
            driver.setTimeoutMilliseconds(timeout);driver.setConnectWaitTimeMilliseconds(connectWait);driver.setDollarWaitTimeMilliseconds(0);
            driver.setConnectionKeepAlive(false);driver.setSyncInitialLocation(false);driver.setAllowUnhomedMotion(false);
            driver.setMotionControlType(MotionControlType.ToolpathFeedRate);driver.setMaxFeedRate(feedRate);
            driver.setUsingLetterVariables(false);driver.setSupportingPreMove(false);driver.setBackslashEscapedCharactersEnabled(false);
            driver.setCompressGcode(false);driver.setRemoveComments(false);
            // Initialize the lazy native send-on-change settings before capturing
            // a baseline; readback then introduces no new protocol model objects.
            driver.setSendOnChangeFeedRate(false);driver.setSendOnChangeAcceleration(false);driver.setSendOnChangeJerk(false);driver.setLoggingGcode(false);
            for(CommandType type:CommandType.values())driver.setCommand(null,type,COMMANDS.get(type));
            ReferenceMachine machine=new ReferenceMachine();machine.addDriver(driver);
            ReferenceHead head=new ReferenceHead();machine.addHead(head);ReferenceNozzle nozzle=new ReferenceNozzle();head.addNozzle(nozzle);
            List<ReferenceControllerAxis> nativeAxes=new ArrayList<>();
            for(AxisSpec spec:axes){
                ReferenceControllerAxis axis=new ReferenceControllerAxis();axis.setName("Loopback-"+spec.type());axis.setType(spec.type());axis.setLetter(spec.letter());axis.setDriver(driver);
                axis.setHomeCoordinate(length(spec.home()));axis.setSoftLimitLow(length(spec.low()));axis.setSoftLimitHigh(length(spec.high()));
                // Native newly created rotational axes migrate on first load to
                // disabled[-180,180] limits. Do not claim configurable rotation
                // limits until that native version/migration lifecycle is covered.
                axis.setSoftLimitLowEnabled(spec.type()!=Axis.Type.Rotation);axis.setSoftLimitHighEnabled(spec.type()!=Axis.Type.Rotation);axis.setResolution(spec.resolution());
                axis.setFeedratePerSecond(length(spec.feed()));axis.setAccelerationPerSecond2(length(spec.acceleration()));axis.setJerkPerSecond3(length(0));
                axis.setPreMoveCommand(null);axis.setLimitRotation(false);axis.setWrapAroundRotation(false);
                machine.addAxis(axis);nativeAxes.add(axis);
                switch(spec.type()){case X:nozzle.setAxisX(axis);break;case Y:nozzle.setAxisY(axis);break;case Z:nozzle.setAxisZ(axis);break;case Rotation:nozzle.setAxisRotation(axis);break;}
            }
            return new Bundle(driver,machine,nozzle,List.copyOf(nativeAxes),endpoint);
        }
    }
    /** Shallow immutable carrier, matching the former record. Factory-created axes
     * are already List.copyOf; no new validation/copy is added to the public constructor. */
    public static final class Bundle {
        private final GcodeDriver driver;private final ReferenceMachine machine;private final ReferenceNozzle nozzle;
        private final List<ReferenceControllerAxis> axes;private final OwnedEndpoint endpoint;
        public Bundle(GcodeDriver driver,ReferenceMachine machine,ReferenceNozzle nozzle,List<ReferenceControllerAxis> axes,OwnedEndpoint endpoint){
            this.driver=driver;this.machine=machine;this.nozzle=nozzle;this.axes=axes;this.endpoint=endpoint;
        }
        public GcodeDriver driver(){return driver;}public ReferenceMachine machine(){return machine;}public ReferenceNozzle nozzle(){return nozzle;}
        public List<ReferenceControllerAxis> axes(){return axes;}public OwnedEndpoint endpoint(){return endpoint;}
        @Override public boolean equals(Object other){if(this==other)return true;if(!(other instanceof Bundle))return false;Bundle b=(Bundle)other;return Objects.equals(driver,b.driver)&&Objects.equals(machine,b.machine)&&Objects.equals(nozzle,b.nozzle)&&Objects.equals(axes,b.axes)&&Objects.equals(endpoint,b.endpoint);}
        @Override public int hashCode(){int result=Objects.hashCode(driver);result=31*result+Objects.hashCode(machine);result=31*result+Objects.hashCode(nozzle);result=31*result+Objects.hashCode(axes);return 31*result+Objects.hashCode(endpoint);}
        @Override public String toString(){return "Bundle[driver="+driver+", machine="+machine+", nozzle="+nozzle+", axes="+axes+", endpoint="+endpoint+"]";}
    }
    private static Length length(double value){return new Length(value,LengthUnit.Millimeters);}
    public static Spec stage(JsonObject json){
        exact(json,"profile","name","command_timeout_ms","connect_wait_ms","max_feed_rate_mm_per_min","axes");
        if(!PROFILE.equals(string(json,"profile",64)))throw fail("UNSUPPORTED_PROFILE","Only the declared owned loopback profile is implemented.");
        String name=string(json,"name",80);if(!name.matches("[ -~]+"))throw fail("INVALID_NAME","Use bounded printable ASCII for the controller name.");
        int timeout=integer(json,"command_timeout_ms",100,2000),wait=integer(json,"connect_wait_ms",0,100),rate=integer(json,"max_feed_rate_mm_per_min",1,6000);
        JsonElement element=json.get("axes");if(element==null||!element.isJsonArray()||element.getAsJsonArray().size()!=4)throw fail("INVALID_AXES","This profile requires exactly X,Y,Z,Rotation.");
        List<AxisSpec> axes=new ArrayList<>();Set<Axis.Type> types=EnumSet.noneOf(Axis.Type.class);Set<String> letters=new HashSet<>();
        for(JsonElement row:element.getAsJsonArray()){
            if(!row.isJsonObject())throw fail("INVALID_AXES","Axis must be a typed object.");JsonObject a=row.getAsJsonObject();
            exact(a,"type","letter","units","home","soft_low","soft_high","feed_per_second","acceleration_per_second2","resolution");
            Axis.Type type;try{type=Axis.Type.valueOf(string(a,"type",16));}catch(Exception error){throw fail("INVALID_AXIS_TYPE","Only X,Y,Z,Rotation are supported.");}
            boolean rotational=type==Axis.Type.Rotation;String letter=string(a,"letter",1),units=string(a,"units",3);
            if(!letter.equals(rotational?"A":type.name())||!units.equals(rotational?"deg":"mm")||!types.add(type)||!letters.add(letter))throw fail("INVALID_AXIS_MAPPING","Each exact profile axis, letter and explicit unit must occur once.");
            double bound=rotational?180:1000,low=number(a,"soft_low",-bound,bound),high=number(a,"soft_high",-bound,bound),home=number(a,"home",-bound,bound);
            if(low>=high||home<low||home>high)throw fail("INVALID_LIMITS","Home must lie inside strictly increasing soft limits.");
            if(rotational&&(low!=-180||high!=180))throw fail("UNSUPPORTED_ROTATION_LIMITS","This prototype preserves native disabled[-180,180] rotational limits; editable rotational soft limits are deferred.");
            double feed=number(a,"feed_per_second",0.001,rotational?360:100),accel=number(a,"acceleration_per_second2",0.001,rotational?3600:1000),resolution=number(a,"resolution",0.0001,0.1);
            axes.add(new AxisSpec(type,letter,units,home,low,high,feed,accel,resolution));
        }
        return new Spec(name,timeout,wait,rate,axes);
    }
    private static void exact(JsonObject json,String... keys){Set<String> wanted=new HashSet<>(Arrays.asList(keys));if(json==null||!json.entrySet().stream().map(Map.Entry::getKey).collect(java.util.stream.Collectors.toSet()).equals(wanted))throw fail("INVALID_FIELDS","Require exactly the documented typed fields; endpoints/templates/properties are not accepted.");}
    private static String string(JsonObject json,String key,int max){JsonElement x=json.get(key);if(x==null||!x.isJsonPrimitive()||!x.getAsJsonPrimitive().isString())throw fail("INVALID_ARGUMENT",key+" must be a string.");String s=x.getAsString();if(s.isEmpty()||s.length()>max||s.indexOf('\0')>=0)throw fail("INVALID_ARGUMENT",key+" exceeds its bound.");return s;}
    private static double number(JsonObject json,String key,double low,double high){JsonElement x=json.get(key);if(x==null||!x.isJsonPrimitive()||!x.getAsJsonPrimitive().isNumber())throw fail("INVALID_ARGUMENT",key+" must be numeric.");double v=x.getAsDouble();if(!Double.isFinite(v)||v<low||v>high)throw fail("OUT_OF_RANGE",key+" is outside its finite bounds.");return v;}
    private static int integer(JsonObject json,String key,int low,int high){number(json,key,low,high);try{return json.get(key).getAsBigDecimal().intValueExact();}catch(ArithmeticException error){throw fail("INVALID_ARGUMENT",key+" must be exactly integral.");}}

    public static void requireFixedProtocol(Bundle bundle){
        bundle.endpoint().check();GcodeDriver d=bundle.driver();
        if(d.getClass()!=GcodeDriver.class||bundle.machine().getClass()!=ReferenceMachine.class||!bundle.machine().getDrivers().contains(d)||d.getCommunicationsType()!=AbstractReferenceDriver.CommunicationsType.tcp||!d.getTcp().getIpAddress().equals(bundle.endpoint().host())||d.getTcp().getPort()!=bundle.endpoint().port()||d.getUnits()!=LengthUnit.Millimeters||d.getTimeoutMilliseconds()<100||d.getTimeoutMilliseconds()>2000||d.getConnectWaitTimeMilliseconds()<0||d.getConnectWaitTimeMilliseconds()>100||d.getLineEndingType()!=ReferenceDriverCommunications.LineEndingType.LF||d.isConnectionKeepAlive()||d.isAllowUnhomedMotion()||d.isSyncInitialLocation()||d.isSupportingPreMove()||d.isBackslashEscapedCharactersEnabled()||d.isCompressGcode()||d.isRemoveComments())throw fail("PROFILE_DRIFT","Native model no longer matches the fixed owned-loopback protocol.");
        for(CommandType type:CommandType.values())if(!Objects.equals(COMMANDS.get(type),d.getCommand(null,type)))throw fail("PROFILE_DRIFT","Native command templates differ from the reviewed profile.");
        if(d.isSendOnChangeFeedRate()||d.isSendOnChangeAcceleration()||d.isSendOnChangeJerk()||d.isLoggingGcode())throw fail("PROFILE_DRIFT","Conditional variable emission and native command logging are unsupported in the fixed wire profile.");
        requireFixedAxes(bundle);
    }

    /** Exact fresh-controller axis profile; refuse alternate native units/semantics.
     * Rotational getters use OpenPnP's neutral millimeter Length token for numeric
     * degrees. Requiring system millimeters keeps that native representation explicit.
     * Runtime commanded/driver coordinates and diagnostic graphs are not calibration.
     */
    private static void requireFixedAxes(Bundle bundle){
        if(Configuration.get().getSystemUnits()!=LengthUnit.Millimeters||bundle.axes().size()!=4)
            throw fail("PROFILE_DRIFT","Owned axes require the fixed millimeter native representation.");
        Set<Axis.Type> types=EnumSet.noneOf(Axis.Type.class);
        for(ReferenceControllerAxis axis:bundle.axes()){
            if(axis==null||axis.getClass()!=ReferenceControllerAxis.class||axis.getDriver()!=bundle.driver()||axis.getType()==null||!types.add(axis.getType())||axis.getUnits()!=LengthUnit.Millimeters)
                throw fail("PROFILE_DRIFT","Require the exact owned controller axis family and driver units.");
            boolean rotation=axis.getType()==Axis.Type.Rotation;
            if(!axis.getLetter().equals(rotation?"A":axis.getType().name()))throw fail("PROFILE_DRIFT","Axis letters must retain their fixed native meaning.");
            double bound=rotation?180:1000;
            double home=fixedLength(axis.getHomeCoordinate(),-bound,bound),low=fixedLength(axis.getSoftLimitLow(),-bound,bound),high=fixedLength(axis.getSoftLimitHigh(),-bound,bound);
            if(low>=high||home<low||home>high||axis.isSoftLimitLowEnabled()==rotation||axis.isSoftLimitHighEnabled()==rotation||(rotation&&(low!=-180||high!=180)))
                throw fail("PROFILE_DRIFT","Retain the declared linear envelope and disabled native rotational limits.");
            fixedLength(axis.getFeedratePerSecond(),0.001,rotation?360:100);
            fixedLength(axis.getAccelerationPerSecond2(),0.001,rotation?3600:1000);
            if(fixedLength(axis.getJerkPerSecond3(),0,0)!=0||!Double.isFinite(axis.getResolution())||axis.getResolution()<0.0001||axis.getResolution()>0.1)
                throw fail("PROFILE_DRIFT","Retain zero jerk and bounded resolution.");
            if(axis.isInvertLinearRotational()||axis.isLimitRotation()||axis.isWrapAroundRotation()||axis.getPreMoveCommand()!=null)
                throw fail("PROFILE_DRIFT","Axis inversion, wrapping, rotation limits and pre-move scripts are unsupported.");
            if(axis.isSafeZoneLowEnabled()||axis.isSafeZoneHighEnabled()||fixedLength(axis.getSafeZoneLow(),0,0)!=0||fixedLength(axis.getSafeZoneHigh(),0,0)!=0)
                throw fail("PROFILE_DRIFT","Safe-zone changes require separate calibration/qualification.");
            if(axis.getBacklashCompensationMethod()!=ReferenceControllerAxis.BacklashCompensationMethod.None||fixedLength(axis.getAcceptableTolerance(),0.025,0.025)!=0.025||fixedLength(axis.getBacklashOffset(),0,0)!=0||fixedLength(axis.getSneakUpOffset(),0,0)!=0||Double.compare(axis.getBacklashSpeedFactor(),0.25)!=0)
                throw fail("PROFILE_DRIFT","Backlash/tolerance settings must retain the fresh native defaults.");
            if(axis.getStepTestGraph()!=null||axis.getBacklashDistanceTestGraph()!=null||axis.getBacklashSpeedTestGraph()!=null)
                throw fail("PROFILE_DRIFT","A calibrated/diagnostic axis cannot be treated as the fresh owned fixture.");
        }
        if(types.size()!=4)throw fail("PROFILE_DRIFT","Require one each X,Y,Z,Rotation.");
    }
    private static double fixedLength(Length value,double low,double high){
        if(value==null||value.getUnits()!=LengthUnit.Millimeters||!Double.isFinite(value.getValue())||value.getValue()<low||value.getValue()>high)
            throw fail("PROFILE_DRIFT","Require finite bounded native Length values with explicit millimeter units; no unit normalization.");
        return value.getValue();
    }
    private static void describeLength(JsonObject row,String name,Length value){
        if(value==null){row.add(name,JsonNull.INSTANCE);row.add(name+"_native_units",JsonNull.INSTANCE);return;}
        row.addProperty(name,value.getValue());row.addProperty(name+"_native_units",value.getUnits()==null?null:value.getUnits().name());
    }

    public static JsonObject describe(Bundle bundle){
        GcodeDriver driver=bundle.driver();JsonObject out=new JsonObject();out.addProperty("id",driver.getId());out.addProperty("name",driver.getName());
        out.addProperty("class",driver.getClass().getName());out.addProperty("system_units",Configuration.get().getSystemUnits().name());out.addProperty("units",driver.getUnits().name());out.addProperty("communications",driver.getCommunicationsType().name());
        out.addProperty("host",driver.getTcp().getIpAddress());out.addProperty("port",driver.getTcp().getPort());out.addProperty("line_ending",driver.getLineEndingType().name());
        out.addProperty("command_timeout_ms",driver.getTimeoutMilliseconds());out.addProperty("connect_wait_ms",driver.getConnectWaitTimeMilliseconds());out.addProperty("max_feed_rate",driver.getMaxFeedRate());
        out.addProperty("connection_keep_alive",driver.isConnectionKeepAlive());out.addProperty("sync_initial_location",driver.isSyncInitialLocation());out.addProperty("allow_unhomed_motion",driver.isAllowUnhomedMotion());
        out.addProperty("motion_control",driver.getMotionControlType().name());out.addProperty("using_letter_variables",driver.isUsingLetterVariables());out.addProperty("supporting_pre_move",driver.isSupportingPreMove());
        out.addProperty("backslash_escapes",driver.isBackslashEscapedCharactersEnabled());out.addProperty("compress_gcode",driver.isCompressGcode());out.addProperty("remove_comments",driver.isRemoveComments());out.addProperty("dollar_wait_ms",driver.getDollarWaitTimeMilliseconds());
        out.addProperty("send_on_change_feedrate",driver.isSendOnChangeFeedRate());out.addProperty("send_on_change_acceleration",driver.isSendOnChangeAcceleration());out.addProperty("send_on_change_jerk",driver.isSendOnChangeJerk());out.addProperty("logging_gcode",driver.isLoggingGcode());out.addProperty("compression_excludes",driver.getCompressionExcludes());
        JsonObject commands=new JsonObject();for(CommandType type:CommandType.values()){String command=driver.getCommand(null,type);if(command!=null)commands.addProperty(type.name(),command);}out.add("commands",commands);
        JsonArray axes=new JsonArray();for(ReferenceControllerAxis axis:bundle.axes()){
            JsonObject a=new JsonObject();a.addProperty("id",axis.getId());a.addProperty("name",axis.getName());a.addProperty("class",axis.getClass().getName());a.addProperty("driver_id",axis.getDriver().getId());a.addProperty("type",axis.getType().name());a.addProperty("letter",axis.getLetter());a.addProperty("units",axis.getType()==Axis.Type.Rotation?"deg":"mm");a.addProperty("driver_units",axis.getUnits().name());
            describeLength(a,"home",axis.getHomeCoordinate());describeLength(a,"soft_low",axis.getSoftLimitLow());describeLength(a,"soft_high",axis.getSoftLimitHigh());a.addProperty("soft_low_enabled",axis.isSoftLimitLowEnabled());a.addProperty("soft_high_enabled",axis.isSoftLimitHighEnabled());
            describeLength(a,"feed_per_second",axis.getFeedratePerSecond());describeLength(a,"acceleration_per_second2",axis.getAccelerationPerSecond2());describeLength(a,"jerk_per_second3",axis.getJerkPerSecond3());a.addProperty("resolution",axis.getResolution());
            describeLength(a,"safe_zone_low",axis.getSafeZoneLow());describeLength(a,"safe_zone_high",axis.getSafeZoneHigh());a.addProperty("safe_zone_low_enabled",axis.isSafeZoneLowEnabled());a.addProperty("safe_zone_high_enabled",axis.isSafeZoneHighEnabled());
            a.addProperty("invert_linear_rotational",axis.isInvertLinearRotational());a.addProperty("limit_rotation",axis.isLimitRotation());a.addProperty("wrap_around_rotation",axis.isWrapAroundRotation());a.addProperty("pre_move_command",axis.getPreMoveCommand());
            a.addProperty("backlash_method",axis.getBacklashCompensationMethod()==null?null:axis.getBacklashCompensationMethod().name());describeLength(a,"acceptable_tolerance",axis.getAcceptableTolerance());describeLength(a,"backlash_offset",axis.getBacklashOffset());describeLength(a,"sneak_up_offset",axis.getSneakUpOffset());a.addProperty("backlash_speed_factor",axis.getBacklashSpeedFactor());
            a.addProperty("step_test_graph_present",axis.getStepTestGraph()!=null);a.addProperty("backlash_distance_graph_present",axis.getBacklashDistanceTestGraph()!=null);a.addProperty("backlash_speed_graph_present",axis.getBacklashSpeedTestGraph()!=null);axes.add(a);
        }out.add("axes",axes);return out;
    }
}
