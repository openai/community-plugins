/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.vision.pipeline.*;
import org.openpnp.vision.pipeline.stages.*;

/** Tests integration with the typed settings adapter and version-one restore admission. */
public final class NativeVisionIntegrationTest {
    static final Gson GSON=new Gson();static int assertions;
    static void check(boolean ok,String message){assertions++;if(!ok)throw new AssertionError(message);}
    static JsonObject json(String text){return new JsonParser().parse(text).getAsJsonObject();}
    static JsonArray rows(JsonObject... values){JsonArray out=new JsonArray();for(JsonObject value:values)out.add(value);return out;}
    interface Action{void run()throws Exception;}
    static void reject(String code,Action action)throws Exception{try{action.run();throw new AssertionError("Expected "+code);}catch(Bridge.Fault e){check(e.code.equals(code),"Expected "+code+", got "+e.code);}}
    public static void main(String[] args)throws Exception{
        Path root=Files.createTempDirectory("openpnp-native-vision-integration-");Configuration.initialize(root.toFile());Configuration c=Configuration.get();c.load();int exit=0;
        try {
            JsonObject clone=json("{\"type\":\"clone_vision_settings\",\"source_vision_settings_id\":\"BVS_Stock\",\"vision_settings_id\":\"BVS_Integrated\",\"name\":\"Integrated\"}");
            check(NativeSettings.types().containsAll(NativeVisionSettings.TYPES),"Native capability inventory includes every vision change");
            double speed=c.getMachine().getSpeed();reject("INVALID_PATCH",()->NativeSettings.stage(c,rows(json("{\"type\":\"set_machine_speed\",\"speed\":0.4}"),clone)));
            check(c.getMachine().getSpeed()==speed&&c.getVisionSettings("BVS_Integrated")==null,"Mixed plan rejection has no model effects");
            NativeSettings.stage(c,rows(clone)).apply();BottomVisionSettings setting=(BottomVisionSettings)c.getVisionSettings("BVS_Integrated");
            check(setting!=null,"Integrated settings adapter creates native clone");
            JsonObject parameter=json("{\"type\":\"set_vision_parameter\",\"vision_settings_id\":\"BVS_Integrated\",\"parameter_name\":\"pThreshold\",\"value\":128}");
            NativeSettings.stage(c,rows(parameter)).apply();
            check(((Number)setting.getPipelineParameterAssignments().get("pThreshold")).intValue()==128,"Integrated adapter stores the exact parameter assignment");
            JsonObject view=GSON.toJsonTree(NativeSettings.describe(c)).getAsJsonObject().getAsJsonObject("vision_settings");check(view.has("profiles"),"Typed configuration includes native vision readback");
            NativeConfigurationSnapshots.Snapshot snapshot=NativeConfigurationSnapshots.capture(c);JsonObject document=GSON.toJsonTree(snapshot.document).getAsJsonObject();
            check(document.getAsJsonArray("excluded_state").toString().contains("vision profiles"),"Version-one restore declares vision readback only");
            document.getAsJsonArray("typed_changes").add(parameter);
            reject("SNAPSHOT_INVALID",()->NativeConfigurationSnapshots.decode(document));
            Threshold threshold=(Threshold)setting.getPipeline().getStage("threshold");threshold.setAuto(true);
            reject("VISION_PARAMETER_OVERRIDDEN",()->NativeSettings.stage(c,rows(parameter)));threshold.setAuto(false);
            CvStage parameterStage=setting.getPipeline().getStage("pThreshold");setting.getPipeline().remove(parameterStage);setting.getPipeline().add(parameterStage);
            reject("UNSUPPORTED_SETTING",()->NativeSettings.stage(c,rows(parameter)));
            check(!c.getMachine().isEnabled()&&!c.getMachine().isHomed(),"Integrated model operations never enable/home");
            System.out.println("OPENPNP_VISION_INTEGRATION_RESULT "+GSON.toJson(Bridge.map("assertions",assertions,"simulation_only",true,"physical_qualification",false,"motion_performed",false,"feed_operations_performed",0)));
        }catch(Throwable e){e.printStackTrace();exit=1;}finally{c.getMachine().close();}System.exit(exit);
    }
}
