/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.awt.image.BufferedImage;
import java.io.*;
import java.lang.reflect.Field;
import java.math.BigDecimal;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import javax.imageio.ImageIO;
import org.opencv.core.Mat;
import org.openpnp.machine.reference.ReferenceMachine;
import org.openpnp.machine.reference.ReferenceNozzle;
import org.openpnp.machine.reference.driver.NullMotionPlanner;
import org.openpnp.machine.reference.driver.ReferenceAdvancedMotionPlanner;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.machine.reference.axis.ReferenceVirtualAxis;
import org.openpnp.machine.reference.camera.ImageCamera;
import org.openpnp.machine.reference.camera.AbstractSettlingCamera.SettleMethod;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.spi.MotionPlanner.CompletionType;
import org.openpnp.spi.Locatable.LocationOption;
import org.openpnp.util.OpenCvUtils;
import org.openpnp.vision.pipeline.CvStage.Result.Circle;
import org.openpnp.vision.pipeline.stages.DetectCircularSymmetry;
import org.openpnp.vision.pipeline.stages.DetectCircularSymmetry.*;

/** Bounded native simulator image measurement. Never saves or applies calibration.
 * Bridge owns control/revision authority, durable effects/artifacts and unknown-outcome fencing. */
public final class NativeCameraScaleMeasurement {
    public static final String RECIPE="camera-planar-scale", POLICY="camera-planar-scale-v1";
    public static final int WIDTH=640, HEIGHT=480, SOURCE_AXIS_LIMIT=8192;
    public static final long SOURCE_PIXEL_LIMIT=32_000_000L, PNG_LIMIT=8L*1024*1024;
    private static final Field SOURCE=field(ImageCamera.class,"source"), RENDER_SCALE=field(ImageCamera.class,"imageUnitsPerPixel");
    private NativeCameraScaleMeasurement() { }

    public static final class Fault extends Exception {
        public final String code;
        Fault(String code,String message){super(message);this.code=code;}
    }
    public static final class Arguments {
        public final String cameraId;public final double displacementMm;public final int diameterPx;
        private Arguments(String id,double d,int diameter){cameraId=id;displacementMm=d;diameterPx=diameter;}
    }
    public interface Callbacks {
        /** Recheck current lease, revision and Bridge admission; must not perform native effects. */
        void checkCurrent() throws Exception;
        /** Must force the pending effect record before returning. */
        void beginEffect(String kind,Map<String,Object> detail) throws Exception;
        /** Must force the matching effect outcome before returning. */
        void endEffect(String kind,Map<String,Object> detail) throws Exception;
        /** Persist the exact PNG and its observation before returning an opaque artifact ID. */
        String persistObservation(byte[] png,Map<String,Object> observation) throws Exception;
    }
    public static Arguments parseArguments(JsonObject json)throws Exception {
        Set<String> fields=new HashSet<>(Arrays.asList("request_id","session_id","expected_config_revision","recipe_id","camera_id","displacement_mm","expected_feature_diameter_px"));
        if(json==null||json.entrySet().size()!=fields.size())fail("INVALID_ARGUMENT","Expected the complete camera-scale command");
        for(Map.Entry<String,JsonElement> e:json.entrySet())if(!fields.contains(e.getKey()))fail("UNKNOWN_FIELD","Unsupported camera-scale argument");
        text(json,"request_id");text(json,"session_id");String revision=text(json,"expected_config_revision");
        if(!revision.matches("cfg-[0-9]+")||!RECIPE.equals(text(json,"recipe_id")))fail("INVALID_ARGUMENT","Invalid recipe or configuration revision");
        String id=text(json,"camera_id");double d=number(json,"displacement_mm");BigDecimal preciseD=new BigDecimal(json.get("displacement_mm").getAsString());
        if(preciseD.compareTo(new BigDecimal("0.5"))<0||preciseD.compareTo(new BigDecimal("2"))>0)fail("INVALID_ARGUMENT","Displacement is outside the exact decimal limits");int diameter;
        try{JsonElement value=json.get("expected_feature_diameter_px");if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isNumber())throw new ArithmeticException();diameter=new BigDecimal(value.getAsString()).intValueExact();}
        catch(RuntimeException e){throw new Fault("INVALID_ARGUMENT","Feature diameter must be an exact integer");}
        limits(d,diameter);return new Arguments(id,d,diameter);
    }
    public static Plan admit(Configuration config,Camera selected,double displacementMm,int diameterPx)throws Exception {
        limits(displacementMm,diameterPx);
        if(config==null||Configuration.get()!=config||config.getMachine()==null||config.getMachine().getClass()!=ReferenceMachine.class)fail("UNSUPPORTED_SIMULATOR","Expected current native ReferenceMachine");
        if(selected==null||selected.getClass()!=ImageCamera.class)fail("UNSUPPORTED_CAMERA","Expected exact native head ImageCamera");
        Plan plan=new Plan(config,(ImageCamera)selected,displacementMm,diameterPx);plan.validate(false);return plan;
    }
    public static final class Plan {
        private final Configuration config;private final ReferenceMachine machine;private final ImageCamera camera;
        private final Head head;private final ReferenceControllerAxis xAxis,yAxis;private final BufferedImage source;
        private final Location renderer,origin;private Location expectedPose;private final String cameraId;private final double displacement;private final int diameter;
        private final String modelFingerprint,sourceFingerprint,sourceReferenceFingerprint;
        private final List<Location> waypoints;private boolean used;private long totalPngBytes;private int moves,captures;
        private final List<Map<String,Object>> observations=new ArrayList<>();
        private Plan(Configuration c,ImageCamera cam,double d,int diameter)throws Exception {
            config=c;machine=(ReferenceMachine)c.getMachine();camera=cam;cameraId=cam.getId();head=cam.getHead();displacement=d;this.diameter=diameter;
            if(machine.getAxes().size()>32||machine.getAllCameras().size()>8||machine.getHeads().size()>4)fail("MODEL_LIMIT","Native graph exceeds measurement limits");
            if(cam.getAxisX()==null||cam.getAxisX().getClass()!=ReferenceControllerAxis.class||cam.getAxisY()==null||cam.getAxisY().getClass()!=ReferenceControllerAxis.class)fail("UNSUPPORTED_AXES","Camera XY must use direct native controller axes");
            xAxis=(ReferenceControllerAxis)cam.getAxisX();yAxis=(ReferenceControllerAxis)cam.getAxisY();
            source=(BufferedImage)SOURCE.get(cam);renderer=(Location)RENDER_SCALE.get(cam);
            if(source==null||renderer==null)fail("CAMERA_SOURCE_UNINITIALIZED","Capture the existing camera once before planning measurement; no lazy source initialization is performed");
            sourceFingerprint=sourceHash(source);sourceReferenceFingerprint=hash(cam.getSourceUri());origin=cam.getLocation().convertToUnits(LengthUnit.Millimeters);finitePose(origin);expectedPose=origin;
            List<Location> points=new ArrayList<>();for(double[] p:new double[][]{{-.5,0},{.5,0},{0,-.5},{0,.5},{.3,-.2},{-.2,.3},{0,0}})points.add(origin.add(new Location(LengthUnit.Millimeters,p[0]*d,p[1]*d,0,0)));
            waypoints=Collections.unmodifiableList(points);modelFingerprint=modelHash(machine);
        }
        public Map<String,Object> describe(){return immutable(map("recipe_id",RECIPE,"policy",POLICY,"camera_id",cameraId,"displacement_mm",displacement,"expected_feature_diameter_px",diameter,"origin",pose(origin),"model_fingerprint",modelFingerprint,"loaded_source_pixel_sha256",sourceFingerprint,"source_reference_sha256",sourceReferenceFingerprint,"source_width",source.getWidth(),"source_height",source.getHeight(),"source_pixel_encoding","ARGB32-big-endian-row-major-with-width-height-v1","capture_api","native-settleAndCapture-without-lighting","maximum_capture_calls",8,"maximum_recipe_move_calls",7,"simulation_only",true,"calibration_applied",false,"hardware_qualified",false));}
        private void validate(boolean executing)throws Exception {
            if(Configuration.get()!=config||config.getMachine()!=machine||camera.getHead()!=head||head==null||!machine.getHeads().contains(head))fail("STALE_MEASUREMENT_PLAN","Native configuration/camera identity changed");
            if(executing&&!machine.isTask(Thread.currentThread()))fail("NATIVE_EXECUTOR_REQUIRED","Measurement must run on the native executor");
            if(machine.isBusy()&&!machine.isTask(Thread.currentThread()))fail("MACHINE_BUSY","Measurement admission requires serialized idle state");
            if(!machine.isEnabled()||!machine.isHomed())fail("MACHINE_NOT_READY","Measurement requires enabled and homed simulator");
            Bridge.verifyNativeSimulatorClasses(machine);
            if(machine.getMotionPlanner()==null||(machine.getMotionPlanner().getClass()!=NullMotionPlanner.class&&machine.getMotionPlanner().getClass()!=ReferenceAdvancedMotionPlanner.class))fail("UNSUPPORTED_MOTION_PLANNER","Expected exact native simulator motion planner");
            if(camera.getLooking()!=Camera.Looking.Down)fail("UNSUPPORTED_CAMERA_ORIENTATION","Measurement requires a down-looking camera");
            if(machine.getDrivers().size()!=1||machine.getDrivers().get(0).getClass()!=NullDriver.class)fail("UNSUPPORTED_DRIVER","One exact native NullDriver is required");
            int identities=0;for(Camera c:machine.getAllCameras())if(c==camera)identities++;else if(Objects.equals(c.getId(),cameraId))fail("CAMERA_IDENTITY","Duplicate native camera ID");
            if(identities!=1||!head.getCameras().contains(camera)||cameraId==null||cameraId.isEmpty()||!cameraId.equals(camera.getId()))fail("CAMERA_IDENTITY","Selected native camera is not uniquely registered");
            if(camera.getAxisX()!=xAxis||camera.getAxisY()!=yAxis||xAxis==yAxis||xAxis.getType()!=Axis.Type.X||yAxis.getType()!=Axis.Type.Y||machine.getAxis(xAxis.getId())!=xAxis||machine.getAxis(yAxis.getId())!=yAxis)fail("UNSUPPORTED_AXES","Direct XY identity or mapping changed");
            for(ReferenceControllerAxis a:Arrays.asList(xAxis,yAxis)){
                if(a.getDriver()!=machine.getDrivers().get(0)||a.getBacklashCompensationMethod()!=ReferenceControllerAxis.BacklashCompensationMethod.None)fail("UNSUPPORTED_AXES","Camera XY must use NullDriver without hidden backlash travel");
            }
            if(camera.getAxisZ()==null||camera.getAxisZ().getClass()!=ReferenceVirtualAxis.class||camera.getAxisRotation()==null||camera.getAxisRotation().getClass()!=ReferenceVirtualAxis.class)fail("UNSUPPORTED_AXES","Camera plane and rotation require fixed native virtual axes");
            if(machine.getAxes().size()>32||machine.getAllCameras().size()>8||machine.getHeads().size()>4)fail("MODEL_LIMIT","Native graph exceeds measurement limits");
            int nozzleCount=0;for(Head h:machine.getHeads())for(Nozzle n:h.getNozzles()){
                if(++nozzleCount>16||n.getClass()!=ReferenceNozzle.class)fail("UNSUPPORTED_NOZZLE","Expected bounded exact native nozzles");
                Length[] zone=n.getSafeZZone();if(n.getPart()!=null||(zone[0]==null&&zone[1]==null)||!n.isInSafeZZone(n.getLocation().getLengthZ()))fail("NOZZLE_NOT_SAFE","All native nozzles must be empty within their native safe-Z zones");
            }
            if(camera.getViewWidth()!=WIDTH||camera.getViewHeight()!=HEIGHT)fail("FRAME_LIMIT","Measurement requires a native 640x480 frame");
            if(camera.isCalibrating()||camera.getCalibration().isEnabled()||camera.getAdvancedCalibration().isEnabled()||camera.getAdvancedCalibration().isOverridingOldTransformsAndDistortionCorrectionSettings()||camera.isEnableUnitsPerPixel3D())fail("UNSUPPORTED_CALIBRATION","Active lens, advanced and 3D camera calibration are unsupported");
            if(camera.getCropWidth()!=0||camera.getCropHeight()!=0||camera.getScaleWidth()!=0||camera.getScaleHeight()!=0||camera.getOffsetX()!=0||camera.getOffsetY()!=0||camera.isDeinterlace()||camera.isFlipX()||camera.isFlipY()||camera.getRotation()!=0)fail("UNSUPPORTED_CAMERA_TRANSFORMS","Expected uncropped planar camera with default orientation");
            if(camera.isWhiteBalanced()||camera.getRedBalance()!=1||camera.getGreenBalance()!=1||camera.getBlueBalance()!=1||camera.getRedGamma()!=1||camera.getGreenGamma()!=1||camera.getBlueGamma()!=1||camera.getSettleGaussianBlur()!=0||camera.isSettleGradients()||camera.getSettleMaskCircle()!=0||camera.getSettleContrastEnhance()!=0||camera.isSettleDiagnostics())fail("UNSUPPORTED_CAMERA_PROCESSING","Measurement requires default color and settling preprocessing");
            if(camera.getSettleMethod()!=SettleMethod.FixedTime||camera.getSettleTimeMs()<600||camera.getSettleTimeMs()>1000)fail("SETTLING_RANGE","Configure FixedTime settling within 600..1000ms first");
            if(camera.getPrimaryFiducial().isInitialized()||camera.getSecondaryFiducial().isInitialized())fail("UNSUPPORTED_SIMULATED_OPTICS","Generated fiducial overlays are unsupported");
            finitePose(camera.getImageOffset());
            if(camera.getSimulatedScale()!=1||camera.getSimulatedRotation()!=0||camera.isSimulatedFlipped()||camera.getSimulatedDistortion()!=0||camera.getSimulatedYRotation()!=0)fail("UNSUPPORTED_SIMULATED_OPTICS","Only the planar unwarped renderer is admitted");
            Location upp=camera.getUnitsPerPixelPrimary().convertToUnits(LengthUnit.Millimeters);finitePose(upp);finitePose(renderer);finitePose(camera.getHeadOffsets());
            if(upp.getX()<=0||upp.getY()<=0||renderer.getX()<=0||renderer.getY()<=0||Math.abs(origin.getZ()-upp.getZ())>1e-9)fail("WORKING_PLANE","Current fixed plane must equal the initialized primary camera scale plane");
            if(SOURCE.get(camera)!=source||RENDER_SCALE.get(camera)!=renderer||!hash(camera.getSourceUri()).equals(sourceReferenceFingerprint)||!sourceHash(source).equals(sourceFingerprint)||!modelHash(machine).equals(modelFingerprint))fail("STALE_MEASUREMENT_PLAN","Native model or loaded source pixels changed");
            Location current=camera.getLocation().convertToUnits(LengthUnit.Millimeters);finitePose(current);
            if(!samePose(current,expectedPose))fail("STALE_MEASUREMENT_PLAN","Native camera pose changed outside the planned effect");
            if(current.getZ()!=origin.getZ()||current.getRotation()!=origin.getRotation())fail("WORKING_PLANE","Camera plane or rotation changed");
            checkWaypoint(origin);for(Location point:waypoints)checkWaypoint(point);
        }
        private void checkWaypoint(Location p)throws Exception {
            finitePose(p);if(p.getX()<-200||p.getX()>500||p.getY()<-200||p.getY()>500||Math.abs(p.getX()-origin.getX())>displacement*.5+1e-9||Math.abs(p.getY()-origin.getY())>displacement*.5+1e-9)fail("WAYPOINT_LIMIT","Fixed recipe exceeds simulator XY bounds");
            AxesLocation raw=camera.toRaw(camera.toHeadLocation(p,LocationOption.Quiet),LocationOption.Quiet);
            for(ReferenceControllerAxis a:Arrays.asList(xAxis,yAxis)){
                double value=raw.getLengthCoordinate(a).convertToUnits(LengthUnit.Millimeters).getValue();
                if(!Double.isFinite(value))fail("WAYPOINT_LIMIT","Nonfinite native axis coordinate");
                double low=a.getSoftLimitLow().convertToUnits(LengthUnit.Millimeters).getValue(),high=a.getSoftLimitHigh().convertToUnits(LengthUnit.Millimeters).getValue();
                if(a.isSoftLimitLowEnabled()&&!Double.isFinite(low)||a.isSoftLimitHighEnabled()&&!Double.isFinite(high)||a.isSoftLimitLowEnabled()&&a.isSoftLimitHighEnabled()&&low>high)fail("NATIVE_SOFT_LIMIT","Invalid native axis soft-limit interval");
                if(a.isSoftLimitLowEnabled()&&value<low||a.isSoftLimitHighEnabled()&&value>high)fail("NATIVE_SOFT_LIMIT","A fixed measurement waypoint exceeds a native axis soft limit");
            }
        }
        private void before(Callbacks cb,String kind,Map<String,Object> detail)throws Exception {cb.checkCurrent();validate(true);cb.beginEffect(kind,immutable(detail));validate(true);cb.checkCurrent();}
        private void move(Callbacks cb,int index,String label)throws Exception {
            Location target=waypoints.get(index);Map<String,Object> detail=map("index",moves+1,"label",label,"target",pose(target));before(cb,"camera-scale-move",detail);moves++;
            camera.moveTo(target,.2);machine.getMotionPlanner().waitForCompletion(camera,CompletionType.WaitForStillstand);expectedPose=target;validate(true);
            if(camera.getLocation().convertToUnits(LengthUnit.Millimeters).getLinearDistanceTo(target)>1e-9)fail("NATIVE_POSITION","Native move returned at another location");
            detail.put("native_pose",pose(camera.getLocation()));detail.put("native_stillstand_returned",true);cb.endEffect("camera-scale-move",immutable(detail));
        }
        private NativeCameraScaleFit.Point capture(Callbacks cb,String label,double dx,double dy)throws Exception {
            Map<String,Object> detail=map("index",captures+1,"label",label,"native_pose",pose(camera.getLocation()));before(cb,"camera-scale-capture",detail);captures++;
            BufferedImage image=camera.settleAndCapture();if(image==null||image.getWidth()!=WIDTH||image.getHeight()!=HEIGHT)fail("CAPTURE_FRAME","Native acquisition returned an unsupported frame");validate(true);
            ByteArrayOutputStream png=new ByteArrayOutputStream();if(!ImageIO.write(image,"PNG",png))fail("PNG_ENCODING","PNG encoder unavailable");byte[] bytes=png.toByteArray();totalPngBytes+=bytes.length;
            if(bytes.length>PNG_LIMIT||totalPngBytes>PNG_LIMIT*8)fail("ARTIFACT_LIMIT","Measurement PNG budget exceeded");
            Mat mat=OpenCvUtils.toMat(image);List<Circle> circles;
            int min=Math.max(3,diameter-5),max=diameter+5;
            try{circles=DetectCircularSymmetry.findCircularSymmetry(mat,WIDTH/2,HEIGHT/2,min,max,220,220,220,2,1.5,0,8,4,SymmetryScore.OverallVarianceVsRingVarianceSum,false,false,new ScoreRange());}finally{mat.release();}
            List<Map<String,Object>> detections=new ArrayList<>();for(Circle c:circles){double score=((SymmetryCircle)c).getScore();if(!Double.isFinite(c.x)||!Double.isFinite(c.y)||!Double.isFinite(c.diameter)||!Double.isFinite(score))fail("NONFINITE_DETECTION","Native detector returned nonfinite values");detections.add(map("x_px",c.x,"y_px",c.y,"diameter_px",c.diameter,"score",score));}
            Map<String,Object> observation=map("index",captures,"label",label,"offset_x_mm",dx,"offset_y_mm",dy,"native_pose",pose(camera.getLocation()),"png_sha256",hex(digest().digest(bytes)),"width",WIDTH,"height",HEIGHT,"bytes",bytes.length,"detections",detections,"capture_return_proves_stability",false,"loaded_source_pixel_sha256",sourceFingerprint);
            String artifact=cb.persistObservation(bytes.clone(),immutable(observation));if(artifact==null||artifact.isEmpty()||artifact.length()>256)fail("ARTIFACT_RECEIPT","Persist callback did not return a bounded artifact identifier");
            observation.put("artifact_id",artifact);Map<String,Object> frozen=immutable(observation);observations.add(frozen);cb.endEffect("camera-scale-capture",frozen);
            if(circles.size()!=1)throw new NativeCameraScaleFit.Rejected(circles.isEmpty()?"TARGET_NOT_FOUND":"AMBIGUOUS_TARGET","Exactly one plausible circular target is required");
            Circle c=circles.get(0);if(((SymmetryCircle)c).getScore()<1.5)throw new NativeCameraScaleFit.Rejected("LOW_SCORE","Circular target score is below the fixed policy");return new NativeCameraScaleFit.Point(c.x,c.y);
        }
        public Map<String,Object> run(Callbacks cb)throws Exception {
            if(cb==null)fail("CALLBACKS_REQUIRED","Native lifecycle callbacks are required");
            synchronized(this){if(used)fail("PLAN_ALREADY_USED","Measurement plans are once-only");used=true;}
            validate(true);cb.checkCurrent();Location initial=camera.getLocation().convertToUnits(LengthUnit.Millimeters);if(!initial.equals(origin))fail("STALE_MEASUREMENT_PLAN","Camera moved after admission");
            Map<String,Object> receipt=new LinkedHashMap<>(describe());List<Map<String,Object>> errors=new ArrayList<>();
            try{
                NativeCameraScaleFit.Point baseline=capture(cb,"baseline",0,0);NativeCameraScaleFit.Point[] train=new NativeCameraScaleFit.Point[4];String[] labels={"train-x-minus","train-x-plus","train-y-minus","train-y-plus"};
                double[][] offsets={{-.5,0},{.5,0},{0,-.5},{0,.5}};
                for(int i=0;i<4;i++){move(cb,i,labels[i]);train[i]=capture(cb,labels[i],offsets[i][0]*displacement,offsets[i][1]*displacement);}
                Location upp=camera.getUnitsPerPixelPrimary().convertToUnits(LengthUnit.Millimeters);NativeCameraScaleFit.Fit fit=NativeCameraScaleFit.estimate(displacement,upp.getX(),upp.getY(),train[0],train[1],train[2],train[3]);
                receipt.put("pixel_displacement_matrix",map("xx",fit.xx,"xy",fit.xy,"yx",fit.yx,"yy",fit.yy,"determinant",fit.determinant));
                for(int i=0;i<2;i++){double dx=(i==0?.3:-.2)*displacement,dy=(i==0?-.2:.3)*displacement;String label="holdout-"+(i+1);move(cb,4+i,label);NativeCameraScaleFit.Point measured=capture(cb,label,dx,dy);NativeCameraScaleFit.Point predicted=fit.predict(baseline,dx,dy);double error=Math.hypot(measured.x-predicted.x,measured.y-predicted.y);errors.add(map("label",label,"error_px",error));fit.check(baseline,dx,dy,measured);}
                move(cb,6,"return-origin");NativeCameraScaleFit.Point last=capture(cb,"final-baseline",0,0);double finalError=fit.check(baseline,0,0,last);cb.checkCurrent();validate(true);
                receipt.put("measurement_status","accepted");receipt.put("proposed_scale_mm_per_px",map("x",fit.unitsPerPixelX,"y",fit.unitsPerPixelY));receipt.put("final_baseline_error_px",finalError);
            }catch(NativeCameraScaleFit.Rejected rejected){receipt.put("measurement_status","rejected");receipt.put("rejection",map("code",rejected.code,"message",rejected.getMessage()));}
            cb.checkCurrent();validate(true);
            receipt.put("observations",new ArrayList<>(observations));receipt.put("heldout_simulator_image_prediction_errors",errors);receipt.put("capture_calls",captures);receipt.put("recipe_move_calls",moves);receipt.put("last_native_pose",pose(camera.getLocation()));receipt.put("independent_physical_residuals",null);receipt.put("physical_scale_verified",false);receipt.put("native_io_hard_deadline",false);return immutable(receipt);
        }
    }
    private static void limits(double d,int diameter)throws Fault {if(!Double.isFinite(d)||d<.5||d>2||diameter<12||diameter>96)fail("INVALID_ARGUMENT","Displacement must be 0.5..2 mm and diameter 12..96 pixels");}
    private static Field field(Class<?> owner,String name){try{Field f=owner.getDeclaredField(name);f.setAccessible(true);return f;}catch(Exception e){throw new ExceptionInInitializerError(e);}}
    private static void finitePose(Location l)throws Fault {if(l==null||!Double.isFinite(l.getX())||!Double.isFinite(l.getY())||!Double.isFinite(l.getZ())||!Double.isFinite(l.getRotation()))fail("NONFINITE_GEOMETRY","Native geometry must be finite");}
    private static String sourceHash(BufferedImage image)throws Exception {
        int w=image.getWidth(),h=image.getHeight();if(w<1||h<1||w>SOURCE_AXIS_LIMIT||h>SOURCE_AXIS_LIMIT||(long)w*h>SOURCE_PIXEL_LIMIT)fail("SOURCE_IMAGE_LIMIT","Loaded source image exceeds bounded dimensions");
        MessageDigest md=digest();md.update("OpenPnP-loaded-source-ARGB32-big-endian-v1\0".getBytes(StandardCharsets.US_ASCII));md.update(ByteBuffer.allocate(8).putInt(w).putInt(h).array());int[] pixels=new int[w];byte[] row=new byte[w*4];
        for(int y=0;y<h;y++){image.getRGB(0,y,w,1,pixels,0,w);for(int x=0,i=0;x<w;x++){int p=pixels[x];row[i++]=(byte)(p>>>24);row[i++]=(byte)(p>>>16);row[i++]=(byte)(p>>>8);row[i++]=(byte)p;}md.update(row);}return hex(md.digest());
    }
    private static String modelHash(ReferenceMachine machine)throws Exception {
        StringWriter buffer=new StringWriter();
        Writer writer=new Writer(){private int count;public void write(char[] c,int off,int len){if(len<0||count>1_048_576-len)throw new IllegalStateException("Native model exceeds measurement fingerprint bound");count+=len;buffer.write(c,off,len);}public void flush(){}public void close(){}};
        Configuration.createSerializer().write(machine,writer);return hash(buffer.toString());
    }
    private static boolean samePose(Location a,Location b){return a.getLinearDistanceTo(b)<=1e-9&&Math.abs(a.getRotation()-b.getRotation())<=1e-9;}

    private static MessageDigest digest()throws Exception{return MessageDigest.getInstance("SHA-256");}
    private static String hash(String s)throws Exception{if(s==null)fail("SOURCE_IDENTITY","Native source reference missing");return hex(digest().digest(s.getBytes(StandardCharsets.UTF_8)));}
    private static String hex(byte[] b){StringBuilder s=new StringBuilder();for(byte v:b)s.append(String.format(Locale.ROOT,"%02x",v&255));return s.toString();}
    private static void fail(String code,String message)throws Fault{throw new Fault(code,message);}
    private static String text(JsonObject j,String key)throws Fault {JsonElement v=j.get(key);if(v==null||!v.isJsonPrimitive()||!v.getAsJsonPrimitive().isString()||v.getAsString().isEmpty()||v.getAsString().length()>200)throw new Fault("INVALID_ARGUMENT","Expected a bounded string: "+key);return v.getAsString();}
    private static double number(JsonObject j,String key)throws Fault {JsonElement v=j.get(key);if(v==null||!v.isJsonPrimitive()||!v.getAsJsonPrimitive().isNumber())throw new Fault("INVALID_ARGUMENT","Expected number: "+key);double n=v.getAsDouble();if(!Double.isFinite(n))throw new Fault("INVALID_ARGUMENT","Expected finite number: "+key);return n;}
    private static Map<String,Object> pose(Location l){l=l.convertToUnits(LengthUnit.Millimeters);return map("x_mm",l.getX(),"y_mm",l.getY(),"z_mm",l.getZ(),"rotation_deg",l.getRotation());}
    private static Map<String,Object> map(Object...p){Map<String,Object> m=new LinkedHashMap<>();for(int i=0;i<p.length;i+=2)m.put((String)p[i],p[i+1]);return m;}
    @SuppressWarnings("unchecked") private static <T>T immutable(T v){if(v instanceof Map){Map<String,Object> c=new LinkedHashMap<>();for(Map.Entry<?,?> e:((Map<?,?>)v).entrySet())c.put((String)e.getKey(),immutable(e.getValue()));return(T)Collections.unmodifiableMap(c);}if(v instanceof List){List<Object> c=new ArrayList<>();for(Object x:(List<?>)v)c.add(immutable(x));return(T)Collections.unmodifiableList(c);}return v;}
}
