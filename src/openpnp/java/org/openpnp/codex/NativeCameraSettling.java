/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.awt.image.BufferedImage;
import java.math.BigDecimal;
import java.util.*;
import org.openpnp.machine.reference.camera.*;
import org.openpnp.machine.reference.camera.AbstractSettlingCamera.SettleMethod;
import org.openpnp.spi.Camera;

/** Typed native settling. Bridge owns simulator admission, serialization, revisions and fault fence.
 * This helper never captures, moves, changes geometry/calibration, runs scripts or selects a source.
 * Native timeout is a loop threshold, not a hard deadline around potentially blocked acquisition. */
public final class NativeCameraSettling {
    public static final String TYPE="set_camera_dynamic_settling";
    public static final int MAX_FRAME_AXIS=2048;
    public static final long MAX_FRAME_PIXELS=4194304;
    public static final List<String> METHODS=Collections.unmodifiableList(Arrays.asList("Maximum","Mean","Euclidean","Square"));
    private NativeCameraSettling() { }

    /** Immutable validated native assignments; outer NativeSettings.Patch supplies single-use apply. */
    public static final class Settings {
        private final ReferenceCamera camera;private final SettleMethod method;private final int timeout,debounce;private final double threshold;private final boolean color;
        private Settings(ReferenceCamera camera,SettleMethod method,int timeout,int debounce,double threshold,boolean color){this.camera=camera;this.method=method;this.timeout=timeout;this.debounce=debounce;this.threshold=threshold;this.color=color;}
        public void apply(){camera.setSettleMethod(method);camera.setSettleTimeoutMs(timeout);camera.setSettleDebounce(debounce);camera.setSettleThreshold(threshold);camera.setSettleFullColor(color);}
    }
    public static Settings stage(ReferenceCamera camera,JsonObject change)throws Exception {
        exact(camera);Set<String> allowed=new HashSet<>(Arrays.asList("type","camera_id","method","timeout_ms","debounce","threshold_percent","full_color"));
        for(Map.Entry<String,JsonElement> e:change.entrySet())if(!allowed.contains(e.getKey()))fail("UNKNOWN_FIELD","Unknown camera settling field: "+e.getKey());
        if(!TYPE.equals(text(change,"type")))fail("INVALID_ARGUMENT","Expected "+TYPE);
        if(!camera.getId().equals(text(change,"camera_id")))fail("INVALID_ARGUMENT","Camera target does not match staged identity");
        String method=text(change,"method");if(!METHODS.contains(method))fail("UNSUPPORTED_CAMERA_SETTLING","Only Maximum, Mean, Euclidean and Square dynamic settling are supported");
        int timeout=integer(change,"timeout_ms",20,5000),debounce=integer(change,"debounce",0,10);
        double threshold=number(change,"threshold_percent",0,100);if(threshold==0)fail("OUT_OF_RANGE","threshold_percent must be greater than zero");
        JsonElement full=change.get("full_color");if(full==null||!full.isJsonPrimitive()||!full.getAsJsonPrimitive().isBoolean())fail("INVALID_ARGUMENT","full_color must be boolean");
        requireDefaultProcessing(camera);frameAdmission(camera,true);
        return new Settings(camera,SettleMethod.valueOf(method),timeout,debounce,threshold,full.getAsBoolean());
    }
    /** Copied native settings: no capture/getWidth() call, so readback does not open a camera. */
    public static Map<String,Object> describe(ReferenceCamera camera) {
        Map<String,Object> result=Bridge.map("settle_method",camera.getSettleMethod()==null?null:camera.getSettleMethod().name(),"settle_time_ms",camera.getSettleTimeMs(),
            "settle_timeout_ms",camera.getSettleTimeoutMs(),"settle_debounce",camera.getSettleDebounce(),"settle_threshold_percent",finite(camera.getSettleThreshold()),"settle_full_color",camera.isSettleFullColor(),
            "settle_gaussian_blur",camera.getSettleGaussianBlur(),"settle_gradients",camera.isSettleGradients(),"settle_mask_circle",finite(camera.getSettleMaskCircle()),"settle_contrast_enhance",finite(camera.getSettleContrastEnhance()),"settle_diagnostics",camera.isSettleDiagnostics(),
            "legacy_calibration_in_progress",camera.isCalibrating(),"dynamic_settling_editable",false,"settling_outcome_reported_by_native_api",false,"native_timeout_is_hard_io_deadline",false);
        try{exact(camera);requireDefaultProcessing(camera);result.putAll(frameAdmission(camera,true));result.put("dynamic_settling_editable",true);}catch(Bridge.Fault failure){result.put("dynamic_settling_unavailable",Bridge.map("code",failure.code,"message",failure.getMessage()));}
        return result;
    }
    public static Map<String,Object> capabilities(){return Bridge.map("change_type",TYPE,"methods",METHODS,"timeout_ms",Bridge.map("minimum",20,"maximum",5000),"debounce",Bridge.map("minimum",0,"maximum",10),"threshold_percent",Bridge.map("exclusive_minimum",0,"maximum",100),"full_color","boolean","preprocessing","default blur/gradients/mask/contrast and disabled diagnostics required","frame_axis_limit",MAX_FRAME_AXIS,"frame_pixel_limit",MAX_FRAME_PIXELS,"advanced_camera_correction","unsupported for bounded raw/settled capture including native preview","legacy_fixed_dwell","retained via set_camera_settling","active_camera_calibration","raw/settled capture and dynamic settings refused; native preview may transform frames","native_timeout_is_hard_io_deadline",false,"capture_return_proves_stability",false,"physical_calibration_validity","not-assessed","hardware_qualified",false);}

    /** Pre-capture admission uses exact camera view getters, never lazy capture-size getters. */
    public static Map<String,Object> admitCapture(Camera raw,String mode)throws Exception {
        ReferenceCamera camera=exact(raw);boolean settled="settled".equals(mode);
        if(!settled&&!"raw".equals(mode))fail("INVALID_MODE","Capture mode must be raw or settled");
        // Even captureRaw may open the native preview worker, which transforms its own frame.
        Map<String,Object> result=frameAdmission(camera,true);
        if(settled){SettleMethod method=camera.getSettleMethod();if(method==null||method==SettleMethod.FixedTime){if(camera.getSettleTimeMs()<0||camera.getSettleTimeMs()>10000)fail("OUT_OF_RANGE","Fixed capture dwell is outside 0..10000 milliseconds");}
            else {stage(camera,currentChange(camera));}}
        result.put("mode",mode);result.put("settling_settings",Bridge.map("method",camera.getSettleMethod()==null?null:camera.getSettleMethod().name(),"timeout_ms",camera.getSettleTimeoutMs(),"debounce",camera.getSettleDebounce(),"threshold_percent",finite(camera.getSettleThreshold()),"full_color",camera.isSettleFullColor(),"fixed_dwell_ms",camera.getSettleTimeMs()));return result;
    }
    public static Map<String,Object> captureReceipt(Camera raw,String mode,long elapsedMs,BufferedImage image,Map<String,Object> admission)throws Exception {
        if(image==null)fail("CAPTURE_NO_IMAGE","Native capture returned no image");dimensions(image.getWidth(),image.getHeight(),"returned image");ReferenceCamera camera=exact(raw);
        Map<String,Object> result=Bridge.map("hardware_qualified",false,"capture_status","capture-returned","capture_mode",mode,"settling_outcome","settled".equals(mode)?"not-reported-by-native-api":"not-requested","image_validity","not-established-by-capture-return","physical_calibration_validity","not-assessed","native_timeout_is_hard_io_deadline",false,"elapsed_ms",elapsedMs,"admission",admission);
        if("settled".equals(mode)&&camera.getSettleMethod()!=null&&METHODS.contains(camera.getSettleMethod().name()))result.put("native_recorded_settle_ms",camera.getRecordedSettleMilliseconds());
        return result;
    }
    private static JsonObject currentChange(ReferenceCamera camera){JsonObject j=new JsonObject();j.addProperty("type",TYPE);j.addProperty("camera_id",camera.getId());j.addProperty("method",camera.getSettleMethod().name());j.addProperty("timeout_ms",camera.getSettleTimeoutMs());j.addProperty("debounce",camera.getSettleDebounce());j.addProperty("threshold_percent",camera.getSettleThreshold());j.addProperty("full_color",camera.isSettleFullColor());return j;}
    private static Map<String,Object> frameAdmission(ReferenceCamera camera,boolean transformed)throws Bridge.Fault {
        ReferenceCamera c=exact(camera);if(c.isCalibrating())fail("CAMERA_CALIBRATION_ACTIVE","Native lens calibration is active; capture or dynamic settings could advance another calibration session, including preview during raw-camera open");int width,height;
        if(c.getClass()==ImageCamera.class){width=((ImageCamera)c).getViewWidth();height=((ImageCamera)c).getViewHeight();}else{width=((SimulatedUpCamera)c).getViewWidth();height=((SimulatedUpCamera)c).getViewHeight();}
        dimensions(width,height,"native frame");int maxWidth=width,maxHeight=height;
        if(transformed){if(c.getAdvancedCalibration().isEnabled()||c.getAdvancedCalibration().isOverridingOldTransformsAndDistortionCorrectionSettings())fail("UNSUPPORTED_CAMERA_TRANSFORMS","Advanced correction needs separate bounded capture qualification");
            int cropW=c.getCropWidth(),cropH=c.getCropHeight(),scaleW=c.getScaleWidth(),scaleH=c.getScaleHeight();
            if(cropW<0||cropH<0||cropW>MAX_FRAME_AXIS||cropH>MAX_FRAME_AXIS)fail("CAMERA_FRAME_LIMIT","Crop dimensions are outside the admitted range");
            if(cropW>0)maxWidth=Math.min(cropW,maxWidth);if(cropH>0)maxHeight=Math.min(cropH,maxHeight);
            if(scaleW!=0||scaleH!=0){dimensions(scaleW,scaleH,"scaled frame");maxWidth=scaleW;maxHeight=scaleH;}
            double rotation=c.getRotation();if(!Double.isFinite(rotation)||Math.abs(rotation)>360)fail("CAMERA_FRAME_LIMIT","Image rotation must be finite within +/-360 degrees");
            if(rotation!=0){double cos=Math.abs(Math.cos(Math.toRadians(rotation))),sin=Math.abs(Math.sin(Math.toRadians(rotation)));int rotatedWidth=(int)Math.ceil(maxWidth*cos+maxHeight*sin)+2,rotatedHeight=(int)Math.ceil(maxWidth*sin+maxHeight*cos)+2;maxWidth=rotatedWidth;maxHeight=rotatedHeight;}
            dimensions(maxWidth,maxHeight,"transformed frame upper bound");
        }
        return Bridge.map("frame_width",width,"frame_height",height,"capture_max_width",Math.max(width,maxWidth),"capture_max_height",Math.max(height,maxHeight),"transformed_width_upper_bound",maxWidth,"transformed_height_upper_bound",maxHeight,"frame_admission_scope","raw acquisition and possible native preview transforms","frame_axis_limit",MAX_FRAME_AXIS,"frame_pixel_limit",MAX_FRAME_PIXELS);
    }
    private static void dimensions(int width,int height,String label)throws Bridge.Fault {if(width<1||height<1||width>MAX_FRAME_AXIS||height>MAX_FRAME_AXIS||(long)width*height>MAX_FRAME_PIXELS)fail("CAMERA_FRAME_LIMIT",label+" exceeds positive 2048-axis / 4194304-pixel bounds");}
    private static void requireDefaultProcessing(ReferenceCamera c)throws Bridge.Fault {if(c.getSettleGaussianBlur()!=0||c.isSettleGradients()||c.getSettleMaskCircle()!=0||c.getSettleContrastEnhance()!=0||c.isSettleDiagnostics())fail("UNSUPPORTED_CAMERA_PROCESSING","Dynamic settling requires default blur, gradients, mask, contrast and disabled diagnostics; values were not changed");}
    private static ReferenceCamera exact(Camera camera)throws Bridge.Fault {if(camera==null||(camera.getClass()!=ImageCamera.class&&camera.getClass()!=SimulatedUpCamera.class))fail("UNSUPPORTED_NATIVE_CLASS","Only exact native ImageCamera and SimulatedUpCamera are supported");return (ReferenceCamera)camera;}
    private static String text(JsonObject j,String key)throws Bridge.Fault {JsonElement value=j.get(key);if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isString())fail("INVALID_ARGUMENT","Expected string: "+key);return value.getAsString();}
    private static double number(JsonObject j,String key,double low,double high)throws Bridge.Fault {JsonElement value=j.get(key);if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isNumber())fail("INVALID_ARGUMENT","Expected number: "+key);double n=value.getAsDouble();if(!Double.isFinite(n)||n<low||n>high)fail("OUT_OF_RANGE","Out of range: "+key);return n;}
    private static int integer(JsonObject j,String key,int low,int high)throws Bridge.Fault {number(j,key,low,high);try{return new BigDecimal(j.get(key).getAsString()).intValueExact();}catch(NumberFormatException|ArithmeticException failure){fail("INVALID_ARGUMENT","Expected exact integral value: "+key);return 0;}}
    private static Double finite(double n){return Double.isFinite(n)?n:null;}
    private static void fail(String code,String message)throws Bridge.Fault {throw new Bridge.Fault(code,message);}
}
