/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;

/** Native model capacity boundaries. No enable, homing, motion, feeding or placement execution. */
public final class NativeGraphLimitsTest {
    static final Gson GSON=new Gson();
    static final List<String> passed=new ArrayList<>();
    static Configuration config;
    static double height;
    public static void main(String[] args)throws Exception {
        Configuration.initialize(Files.createTempDirectory("openpnp-native-graph-limits-").toFile());
        config=Configuration.get();config.load();height=config.getPart("R0603-1K").getHeight().convertToUnits(LengthUnit.Millimeters).getValue();int exit=0;
        try {
            JsonObject exact=base(10);JsonArray roots=exact.getAsJsonArray("instances"),panels=exact.getAsJsonArray("panels");
            for(int p=0;p<9;p++){JsonArray children=new JsonArray();for(int n=0;n<100;n++)children.add(instance("child-"+n,"board","b"));panels.add(panel("p"+p,children));roots.add(instance("root-panel-"+p,"panel","p"+p));}
            for(int n=0;n<91;n++)roots.add(instance("root-board-"+n,"board","b"));
            Job job=CanonicalJobImporter.load(config,exact);
            require(count(job.getRootPanelLocation())==1000,"exactly 1000 native expanded instances");
            require(job.getBoardLocations().size()==991,"991 native board instances retain shared definition");
            require(job.getBoardLocations().stream().mapToInt(b->b.getBoard().getPlacements().size()).sum()==9910,"all 9910 expanded placements retained");
            passed.add("1000 expanded native instances and 100 roots accepted without enabling the machine");

            JsonObject excess=new JsonParser().parse(exact.toString()).getAsJsonObject();
            JsonArray excessRoots=new JsonArray();for(int n=0;n<99;n++)excessRoots.add(excess.getAsJsonArray("instances").get(n));
            excessRoots.add(instance("last-panel","panel","one"));excess.add("instances",excessRoots);
            JsonArray one=new JsonArray();one.add(instance("a","board","b"));excess.getAsJsonArray("panels").add(panel("one",one));
            reject(excess,"INPUT_TOO_LARGE");passed.add("expanded instance overflow rejects without committing staged parts");
            Job deepest=CanonicalJobImporter.load(config,nested(8));require(count(deepest.getRootPanelLocation())==9,"depth-eight graph preserved");
            reject(nested(9),"INPUT_TOO_LARGE");passed.add("depth eight accepted and depth nine rejected");

            JsonObject placementLimit=base(10000);placementLimit.getAsJsonArray("instances").add(instance("board","board","b"));
            require(CanonicalJobImporter.load(config,placementLimit).getBoardLocations().get(0).getBoard().getPlacements().size()==10000,"10000 definition placements retained");
            placementLimit.getAsJsonArray("instances").add(instance("second-board","board","b"));
            reject(placementLimit,"INPUT_TOO_LARGE");passed.add("10000 native placements accepted and expanded placement overflow rejected");
            require(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"capacity tests must stay passive");
            System.out.println("OPENPNP_NATIVE_GRAPH_LIMITS_RESULT "+GSON.toJson(Bridge.map("passed",passed,"upstream_commit",Bridge.UPSTREAM,"simulation_only",true,"placement_execution_performed",false)));
        }catch(Throwable error){error.printStackTrace();exit=1;}finally{config.getMachine().close();}System.exit(exit);
    }
    static int count(PanelLocation root){int count=0;for(PlacementsHolderLocation<?> child:root.getChildren()){count++;if(child instanceof PanelLocation)count+=count((PanelLocation)child);}return count;}
    static JsonObject nested(int panels){JsonObject input=base(1);for(int n=0;n<panels;n++){JsonArray children=new JsonArray();children.add(instance("child",n==panels-1?"board":"panel",n==panels-1?"b":"p"+(n+1)));input.getAsJsonArray("panels").add(panel("p"+n,children));}input.getAsJsonArray("instances").add(instance("root","panel","p0"));return input;}
    static void reject(JsonObject input,String code)throws Exception {
        int before=config.getParts().size();JsonObject newPart=object("id","UNCOMMITTED-CAPACITY-PART","packageId","R0603","heightMm",height);input.getAsJsonArray("parts").add(newPart);
        try{CanonicalJobImporter.load(config,input);throw new AssertionError("Expected "+code);}catch(Bridge.Fault error){require(code.equals(error.code),"Expected "+code+" got "+error.code);}
        require(config.getParts().size()==before&&config.getPart("UNCOMMITTED-CAPACITY-PART")==null,"failed graph committed a staged part");
    }
    static JsonObject base(int placements){JsonObject input=object("schemaVersion",1,"id","capacity","units","mm","coordinateConvention","openpnp-top-view");JsonArray parts=new JsonArray();parts.add(object("id","R0603-1K","packageId","R0603","heightMm",height));input.add("parts",parts);JsonArray rows=new JsonArray();for(int n=0;n<placements;n++)rows.add(object("ref","R"+n,"partId","R0603-1K","packageId","R0603","heightMm",height,"x",2,"y",3,"z",0,"rotation",0,"enabled",true,"side","top","type","placement"));JsonObject board=object("id","b","widthMm",20,"heightMm",20);board.add("placements",rows);JsonArray boards=new JsonArray();boards.add(board);input.add("boards",boards);input.add("panels",new JsonArray());input.add("instances",new JsonArray());return input;}
    static JsonObject panel(String id,JsonArray children){JsonObject panel=object("id",id,"widthMm",20,"heightMm",20);panel.add("children",children);return panel;}
    static JsonObject instance(String id,String kind,String definition){return object("id",id,"kind",kind,"definitionId",definition,"x",0,"y",0,"z",0,"rotation",0,"side","top","enabled",true);}
    static JsonObject object(Object...pairs){return GSON.toJsonTree(Bridge.map(pairs)).getAsJsonObject();}
    static void require(boolean condition,String message){if(!condition)throw new AssertionError(message);}
}
