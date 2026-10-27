/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.util.*;
import java.nio.ByteBuffer;
import org.w3c.dom.*;

/** Admission before native deserialization. Bounds include repeated references, not only ZIP bytes. */
public final class NativePortableLimits {
    private NativePortableLimits() {}
    public static Map<String,Object> describe(){return Bridge.map("xml_elements_total",30000,"cameras",2,"image_cameras",1,"camera_frame_axis",2048,"camera_frame_pixels_total",4194304,"referenced_image_pixels_total",32000000,"archive_resource_pixels_total",32000000,"archive_resources",4,"heads",2,"nozzles",4,"axes",16,"drivers",2,"feeders",256,"parts",512,"packages",256,"vision_settings",128,"pipelines",256,"stages_total",1024,"stages_per_pipeline",64,"gaussian_kernel",63,"vision_super_sampling",16,"footprint_template_axis",2048,"advanced_camera_calibration","disabled-only","camera_settling","FixedTime only; dynamic settings require separate portable qualification","array_length",1024,"array_elements_total",32768,"vision_pipeline_profile",NativePortablePipelines.describe(),"board_library",NativePortableBoardLibrary.describe(),"hardware_qualified",false);}
    public static Map<String,Object> describePanelLibrary(){return NativePortablePanelLibrary.describe();}
    static void validate(Map<String,byte[]> entries)throws Exception {validate(entries,false);}
    static void validate(Map<String,byte[]> entries,boolean panels)throws Exception {
        double minimumUpp=Double.POSITIVE_INFINITY,footprintRadius=0;
        Map<String,Integer> counts=new HashMap<>();long imagePixels=0,framePixels=0,arrayElements=0;int elements=0;
        Map<String,Integer> max=Map.ofEntries(Map.entry("camera",2),Map.entry("head",2),Map.entry("nozzle",4),Map.entry("nozzle-tip",16),Map.entry("axis",16),Map.entry("driver",2),Map.entry("feeder",256),Map.entry("part",512),Map.entry("package",256),Map.entry("vision-settings",128),Map.entry("pipeline",256),Map.entry("cv-pipeline",256),Map.entry("cv-stage",1024));
        for(String file:NativePortableConfiguration.CONFIG){Document document=NativePortableConfiguration.xml(entries.get("config/"+file));NativePortableConfiguration.validate(document,file,panels);NodeList all=document.getElementsByTagName("*");
            elements+=all.getLength();if(elements>30000)reject("Native XML model exceeds total element admission bound");
            for(int i=0;i<all.getLength();i++){Element e=(Element)all.item(i);classContext(e,file);String tag=e.getTagName();counts.merge(tag,1,Integer::sum);if(max.containsKey(tag)&&counts.get(tag)>max.get(tag))reject("Too many native "+tag+" records");
                if(e.hasAttribute("length")){long length=integer(e.getAttribute("length"),0,1024,"array length");arrayElements+=length;if(arrayElements>32768)reject("Native array allocation budget exceeded");}
                if("pipeline".equals(tag)||"cv-pipeline".equals(tag)){counts.merge("pipelines-total",1,Integer::sum);if(counts.get("pipelines-total")>256)reject("Native total pipeline count exceeded");if(e.getElementsByTagName("cv-stage").getLength()>64)reject("Native pipeline stage count exceeded");NativePortablePipelines.validate(e);}
                if("footprint".equals(tag)){
                    double units=units(e.getAttribute("units")),x=number(e,"body-width",0,0,1000)/2,y=number(e,"body-height",0,0,1000)/2;
                    NodeList pads=e.getElementsByTagName("pad");if(pads.getLength()>512)reject("Footprint pad count exceeds bounded rendering profile");for(int j=0;j<pads.getLength();j++){Element pad=(Element)pads.item(j);double span=(number(pad,"width",0,0,1000)+number(pad,"height",0,0,1000))/2;x=Math.max(x,Math.abs(number(pad,"x",0,-1000,1000))+span);y=Math.max(y,Math.abs(number(pad,"y",0,-1000,1000))+span);}footprintRadius=Math.max(footprintRadius,Math.hypot(x,y)*units);
                }
                if(Set.of("raw-cropped-image-width","raw-cropped-image-height").contains(tag))numeric(e.getTextContent(),0,2048,tag);
                if("advanced-calibration".equals(tag)){
                    if("true".equals(e.getAttribute("enabled"))||"true".equals(e.getAttribute("overriding-old-transforms-and-distortion-correction-settings")))reject("Advanced camera correction must remain disabled in the portable profile");
                    attribute(e,"raw-cropped-image-width",0,0,2048);attribute(e,"raw-cropped-image-height",0,0,2048);
                }
                if("camera".equals(tag)){
                    if(e.hasAttribute("settle-method")&&!"FixedTime".equals(e.getAttribute("settle-method")))reject("Portable adoption currently supports FixedTime settling only; dynamic or Motion source settings are preserved and refused");
                    String type=e.getAttribute("class");if(!Set.of("org.openpnp.machine.reference.camera.ImageCamera","org.openpnp.machine.reference.camera.SimulatedUpCamera").contains(type))reject("Camera must declare its exact simulator class");
                    if("true".equals(e.getAttribute("enable-units-per-pixel-3-d")))reject("Portable rendering requires planar units-per-pixel");NodeList locations=e.getElementsByTagName("units-per-pixel");if(locations.getLength()!=1)reject("Camera needs exactly one bounded planar pixel geometry");Element upp=(Element)locations.item(0);double scale=units(upp.getAttribute("units"));minimumUpp=Math.min(minimumUpp,Math.min(number(upp,"x",0,0.000001,1000),number(upp,"y",0,0.000001,1000))*scale);attribute(e,"settle-gaussian-blur",0,0,63);
                    long w=attribute(e,"width",640,1,2048),h=attribute(e,"height",480,1,2048);framePixels+=w*h;if(framePixels>4194304)reject("Native camera frame allocation budget exceeded");
                    for(String field:List.of("crop-width","crop-height","scale-width","scale-height"))attribute(e,field,0,0,2048);
                    attribute(e,"capture-try-count",4,1,10);attribute(e,"capture-try-timeout-ms",2000,1,30000);attribute(e,"settle-timeout-ms",500,0,30000);attribute(e,"settle-time-ms",0,0,30000);
                    if(type.endsWith(".ImageCamera")){counts.merge("image-camera",1,Integer::sum);if(counts.get("image-camera")>1)reject("Only one native ImageCamera is supported");NodeList uris=e.getElementsByTagName("source-uri");if(uris.getLength()!=1)reject("Image camera needs exactly one bounded resource");String uri=uris.item(0).getTextContent();if(!uri.matches("codex-resource:[a-f0-9]{64}"))reject("Image camera resource is not content-addressed");byte[] image=entries.get("resources/"+uri.substring(15)+".png");if(image==null||image.length<24)reject("Camera resource absent");imagePixels+=(long)ByteBuffer.wrap(image,16,4).getInt()*ByteBuffer.wrap(image,20,4).getInt();if(imagePixels>32000000)reject("Native image allocation budget exceeded, including repeated resource use");}
                }
                if("cv-stage".equals(tag))stage(e);
                if("pipeline-parameter-assignments".equals(tag))assignments(e);
                for(int a=0;a<e.getAttributes().getLength();a++){Node value=e.getAttributes().item(a);if(value.getNodeValue().length()>8192)reject("Native attribute too long");}
            }
        }
        if(!Double.isFinite(minimumUpp)||minimumUpp<0.000001||10/minimumUpp>4096||Math.max(footprintRadius*3/minimumUpp,footprintRadius*2/minimumUpp+6)>2048)reject("Footprint/pixel-geometry combination exceeds the bounded native template allocation");
        if(counts.getOrDefault("camera",0)==0||counts.getOrDefault("driver",0)==0||counts.getOrDefault("head",0)==0)reject("A complete native simulator model is required");
    }
    static void stage(Element stage)throws Exception {
        String type=stage.getAttribute("class");
        if(type.endsWith(".ImageCapture"))attribute(stage,"count",1,1,4);
        if(type.endsWith(".BlurGaussian")){long kernel=attribute(stage,"kernel-size",3,3,63);if((kernel&1)==0)reject("Gaussian kernel must be odd");}
        if(type.endsWith(".DetectCircularSymmetry")||type.endsWith(".DetectRectlinearSymmetry")){attribute(stage,"super-sampling",1,1,16);attribute(stage,"sub-sampling",1,1,32);attribute(stage,"max-target-count",1,1,64);for(String n:List.of("min-diameter","max-diameter","max-distance","search-width","search-height"))number(stage,n,0,0,4096);}
        if(type.endsWith(".DetectRectlinearSymmetry")){attribute(stage,"smoothing",5,0,63);number(stage,"expected-angle",0,-360,360);number(stage,"search-angle",45,0,180);number(stage,"max-width",100,0,2048);number(stage,"max-height",100,0,2048);number(stage,"search-distance",100,0,2048);number(stage,"min-feature-size",40,0,4096);number(stage,"gamma",2.5,0,8);attribute(stage,"threshold",128,0,255);bool(stage,"symmetric-left-right");bool(stage,"symmetric-upper-lower");}
        if(type.endsWith(".FilterContours"))number(stage,"min-area",0,0,4194304);
        if(type.endsWith(".MatchTemplate"))number(stage,"max-distance",10000,0,10000);
        if(type.endsWith(".Threshold")){attribute(stage,"threshold",0,0,255);bool(stage,"auto");bool(stage,"invert");}
        if(type.endsWith(".MaskHsv")){for(String channel:List.of("hue","saturation","value")){long low=attribute(stage,channel+"-min",0,0,255),high=attribute(stage,channel+"-max",255,0,255);if(low>high)reject("HSV channel bounds reversed");}bool(stage,"invert");}
        if(type.endsWith(".ParameterNumeric")||type.endsWith(".ParameterBool"))if(!stage.getAttribute("name").matches("[A-Za-z_][A-Za-z0-9_]{0,63}"))reject("Pipeline parameter name is outside the bounded non-dotted namespace");
        if(type.endsWith(".ParameterNumeric")){
            String property=stage.getAttribute("property-name");double max;
            switch(property){case "threshold":max=255;break;case "minArea":case "minFeatureSize":max=1;break;case "maxDistance":max=10;break;default:reject("Numeric pipeline parameter target is outside the bounded portable profile");return;}
            double min=number(stage,"minimum-value",0,0,max),high=number(stage,"maximum-value",max,0,max);if(min>high)reject("Numeric parameter bounds reversed");number(stage,"default-value",0,min,high);
        }
        if(type.endsWith(".ParameterBool")&&!Set.of("auto","invert","symmetricLeftRight","symmetricUpperLower").contains(stage.getAttribute("property-name")))reject("Boolean pipeline parameter target is outside portable profile");
        if(type.endsWith(".CreateFootprintTemplateImage")){number(stage,"max-width",0,0,2048);number(stage,"max-height",0,0,2048);number(stage,"x-offset",0,-2048,2048);number(stage,"y-offset",0,-2048,2048);number(stage,"rotation",0,-360,360);}
    }
    static void bool(Element e,String name)throws Exception {if(e.hasAttribute(name)&&!Set.of("true","false").contains(e.getAttribute(name)))reject("Expected native boolean "+name);}
    static void assignments(Element assignments)throws Exception {
        Node parent=assignments.getParentNode();if(!(parent instanceof Element))reject("Parameter assignments need native vision settings context");Element settings=(Element)parent;NodeList stages=settings.getElementsByTagName("cv-stage");Map<String,Element> params=new HashMap<>();for(int i=0;i<stages.getLength();i++){Element stage=(Element)stages.item(i);if(stage.getAttribute("class").endsWith(".ParameterNumeric")||stage.getAttribute("class").endsWith(".ParameterBool"))params.put(stage.getAttribute("name"),stage);}
        NodeList entries=assignments.getElementsByTagName("entry");if(entries.getLength()>32)reject("Too many parameter assignments");for(int i=0;i<entries.getLength();i++){Element entry=(Element)entries.item(i);NodeList names=entry.getElementsByTagName("string"),values=entry.getElementsByTagName("object");if(names.getLength()!=1||values.getLength()!=1)reject("Only declared scalar parameter assignments are portable");Element parameter=params.get(names.item(0).getTextContent());if(parameter==null)reject("Undeclared pipeline property override is not portable");Element value=(Element)values.item(0);String type=value.getAttribute("class"),text=value.getTextContent();if(parameter.getAttribute("class").endsWith(".ParameterBool")){if(!type.equals("java.lang.Boolean")||!Set.of("true","false").contains(text))reject("Expected boolean parameter assignment");}else{if(!Set.of("java.lang.Integer","java.lang.Double").contains(type))reject("Expected bounded numeric parameter assignment");double min=Double.parseDouble(parameter.getAttribute("minimum-value")),max=Double.parseDouble(parameter.getAttribute("maximum-value"));numeric(text,min,max,"parameter assignment");}}
    }
    static double units(String units)throws Exception {switch(units){case "Millimeters":return 1;case "Centimeters":return 10;case "Meters":return 1000;case "Microns":return .001;case "Inches":return 25.4;case "Feet":return 304.8;default:reject("Unsupported native length units");return 0;}}
    static double number(Element e,String name,double fallback,double min,double max)throws Exception{return e.hasAttribute(name)?numeric(e.getAttribute(name),min,max,name):fallback;}
    static double numeric(String text,double min,double max,String field)throws Exception {try{double n=Double.parseDouble(text);if(!Double.isFinite(n)||n<min||n>max)reject("Native "+field+" exceeds bounded rendering profile");return n;}catch(NumberFormatException failure){reject("Invalid native numeric field "+field);return 0;}}
    static void classContext(Element e,String file)throws Exception {
        String type=e.getAttribute("class");if(!type.startsWith("org.openpnp."))return;
        String tag;
        if(type.startsWith("org.openpnp.vision.pipeline.stages."))tag="cv-stage";
        else if(type.equals("org.openpnp.model.BottomVisionSettings")||type.equals("org.openpnp.model.FiducialVisionSettings"))tag="vision-settings";
        else if(type.startsWith("org.openpnp.machine.reference.axis."))tag="axis";
        else if(type.startsWith("org.openpnp.machine.reference.feeder."))tag="feeder";
        else if(type.endsWith(".ImageCamera")||type.endsWith(".SimulatedUpCamera"))tag="camera";
        else {Map<String,String> expected=Map.ofEntries(Map.entry("ReferenceMachine","machine"),Map.entry("ReferenceHead","head"),Map.entry("ReferenceNozzle","nozzle"),Map.entry("ReferenceNozzleTip","nozzle-tip"),Map.entry("ReferenceActuator","actuator"),Map.entry("ReferencePnpJobProcessor","pnp-job-processor"),Map.entry("ReferencePnpJobProcessor$SimplePnpJobPlanner","planner"),Map.entry("NullDriver","driver"),Map.entry("NullMotionPlanner","motion-planner"),Map.entry("AutoFocusProvider","focus-provider"),Map.entry("OpenCvVisionProvider","vision-provider"),Map.entry("ReferenceBottomVision","part-alignment"),Map.entry("ReferenceFiducialLocator","fiducial-locator"));tag=expected.get(type.substring(type.lastIndexOf('.')+1));}
        if(tag==null||!tag.equals(e.getTagName())||(!file.equals("machine.xml")&&!file.equals("vision-settings.xml"))||(file.equals("vision-settings.xml")&&!Set.of("vision-settings","cv-stage").contains(tag)))reject("Native class is outside its supported configuration field: "+type+" in "+e.getTagName());
        for(Node ancestor=e.getParentNode();ancestor instanceof Element;ancestor=ancestor.getParentNode())if(Set.of("object","entry","pipeline-parameter-assignments").contains(((Element)ancestor).getTagName()))reject("Native objects are forbidden in generic parameter/state values");
    }
    static void resourceBudget(Map<String,byte[]> entries)throws Exception {
        long pixels=0;int count=0;for(var entry:entries.entrySet())if(entry.getKey().startsWith("resources/")){
            byte[] bytes=entry.getValue();if(bytes.length<24)reject("Truncated camera resource");long width=ByteBuffer.wrap(bytes,16,4).getInt(),height=ByteBuffer.wrap(bytes,20,4).getInt();if(width<1||height<1||width>8192||height>8192)reject("Native resource dimensions exceed admission bounds");pixels+=width*height;if(++count>4||pixels>32000000)reject("Archive resource decode budget exceeded, including unreferenced images");
        }
    }
    static long attribute(Element e,String name,long fallback,long min,long max)throws Exception{return e.hasAttribute(name)?integer(e.getAttribute(name),min,max,name):fallback;}
    static long integer(String text,long min,long max,String field)throws Exception {try{long n=Long.parseLong(text);if(n<min||n>max)reject("Native "+field+" exceeds allocation/admission bounds");return n;}catch(NumberFormatException failure){reject("Native "+field+" must be bounded integer");return 0;}}
    static void reject(String message)throws Bridge.Fault {throw new Bridge.Fault("PORTABLE_PROFILE_LIMIT",message);}
}
