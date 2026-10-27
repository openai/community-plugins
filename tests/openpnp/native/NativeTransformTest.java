/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.util.Utils2D;

/** Native canonical graph transforms; expected coordinates are explicit independent golden values. */
public final class NativeTransformTest {
    static final Gson GSON=new Gson();static final List<String> passed=new ArrayList<>();
    public static void main(String[]args)throws Exception {
        Configuration.initialize(Files.createTempDirectory("openpnp-native-transform-").toFile());Configuration.get().load();int code=0;
        try {
            check(false,false,90,97,202,100,"top board rotation");
            check(true,false,0,118,203,10,"bottom board width reflection with side-specific rotation");
            check(true,true,0,172,223,10,"nested double-bottom reflection");
            System.out.println("OPENPNP_NATIVE_TRANSFORM_RESULT "+GSON.toJson(Bridge.map("passed",passed,"upstream_commit",Bridge.UPSTREAM)));
        }catch(Throwable e){e.printStackTrace();code=1;}finally{Configuration.get().getMachine().close();}System.exit(code);
    }
    static void check(boolean bottom,boolean panel,double rotation,double expectedX,double expectedY,double expectedRotation,String name)throws Exception {
        JsonArray parts=new JsonArray();parts.add(object("id","R0603-1K","packageId","R0603","heightMm",0.75));
        JsonArray placements=new JsonArray();placements.add(object("ref","R1","partId","R0603-1K","packageId","R0603","heightMm",0.75,"x",2,"y",3,"z",0,"rotation",10,"enabled",true,"side",bottom&&!panel?"bottom":"top","type","placement"));
        JsonObject board=object("id","b","widthMm",20,"heightMm",10);board.add("placements",placements);JsonArray boards=new JsonArray();boards.add(board);
        JsonArray panels=new JsonArray(),instances=new JsonArray();
        if(panel){JsonArray children=new JsonArray();children.add(instance("child","board","b",10,20,0,"bottom"));JsonObject p=object("id","p","widthMm",100,"heightMm",80);p.add("children",children);panels.add(p);instances.add(instance("root","panel","p",100,200,0,"bottom"));}
        else instances.add(instance("root","board","b",100,200,rotation,bottom?"bottom":"top"));
        JsonObject input=object("schemaVersion",1,"id","transform","units","mm","coordinateConvention","openpnp-top-view");input.add("parts",parts);input.add("boards",boards);input.add("panels",panels);input.add("instances",instances);
        Job job=CanonicalJobImporter.load(Configuration.get(),input);BoardLocation location=job.getBoardLocations().get(0);Placement placement=location.getBoard().getPlacements().get(0);
        Location actual=Utils2D.calculateBoardPlacementLocation(location,placement).convertToUnits(LengthUnit.Millimeters);
        if(Math.abs(actual.getX()-expectedX)>1e-8||Math.abs(actual.getY()-expectedY)>1e-8||Math.abs(actual.getRotation()-expectedRotation)>1e-8)throw new AssertionError(name+": "+actual);
        if(placement.getSide()!=location.getGlobalSide())throw new AssertionError("native physical side mismatch");passed.add(name);
    }
    static JsonObject instance(String id,String kind,String definition,double x,double y,double rotation,String side){return object("id",id,"kind",kind,"definitionId",definition,"x",x,"y",y,"z",0,"rotation",rotation,"side",side,"enabled",true);}
    static JsonObject object(Object...pairs){return GSON.toJsonTree(Bridge.map(pairs)).getAsJsonObject();}
}
