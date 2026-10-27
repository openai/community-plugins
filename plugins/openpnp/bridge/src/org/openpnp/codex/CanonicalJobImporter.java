/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.model.Abstract2DLocatable.Side;

/** Converts bounded canonical JSON to actual OpenPnP models, without deserializing executable XML. */
public final class CanonicalJobImporter {
    private final Configuration config;
    private final NativePartBindings existingParts;
    private final Map<String,PartSpec> parts=new LinkedHashMap<>();
    private final IdentityHashMap<Placement,PartSpec> bindings=new IdentityHashMap<>();
    private static final class PartSpec {
        final String id,name;final org.openpnp.model.Package pkg;final double height;Part model;
        PartSpec(String id,String name,org.openpnp.model.Package pkg,double height,Part model){this.id=id;this.name=name;this.pkg=pkg;this.height=height;this.model=model;}
    }
    private static String nativeKey(String id){return id.toUpperCase();}
    private final Map<String,Board> boards=new LinkedHashMap<>();
    private final Map<String,JsonObject> panels=new LinkedHashMap<>();
    private int instanceCount;
    private int placementCount;
    private int expandedPlacementCount;
    private CanonicalJobImporter(Configuration config){this(config,null);}
    private CanonicalJobImporter(Configuration config,NativePartBindings existingParts){this.config=config;this.existingParts=existingParts;}
    public static Job load(Configuration config,JsonObject canonical)throws Exception {
        CanonicalJobImporter parser=new CanonicalJobImporter(config);
        Job job=parser.parse(canonical);
        // Commit new part definitions only after the entire graph validates and native models build.
        for(PartSpec spec:parser.parts.values())if(spec.model==null){Part part=new Part(spec.id);part.setPackage(spec.pkg);part.setHeight(new Length(spec.height,LengthUnit.Millimeters));part.setName(spec.name);config.addPart(part);spec.model=part;}
        for(Map.Entry<Placement,PartSpec> binding:parser.bindings.entrySet())binding.getKey().setPart(binding.getValue().model);
        return job;
    }
    /** Explicit, import-local resolution; null/empty bindings never select legacy creation. */
    public static Job load(Configuration config,JsonObject canonical,JsonArray partBindings)throws Exception {
        return loadExistingParts(config,canonical,NativePartBindings.resolve(config,canonical,partBindings));
    }
    /** Use a frozen resolver after caller admission; no model/library publication occurs here. */
    public static Job loadExistingParts(Configuration config,JsonObject canonical,NativePartBindings partBindings)throws Exception {
        if(partBindings==null)fail("INVALID_PART_BINDINGS","Existing-parts resolver is required");
        partBindings.validate(config,canonical);
        CanonicalJobImporter parser=new CanonicalJobImporter(config,partBindings);
        Job job=parser.parse(canonical);
        partBindings.validate(config,canonical);
        return job;
    }
    private Job parse(JsonObject input)throws Exception {
        if(num(input,"schemaVersion",1,1)!=1||!"mm".equals(str(input,"units"))||!"openpnp-top-view".equals(str(input,"coordinateConvention")))fail("INCOMPATIBLE_SCHEMA","Expected canonical schema 1, mm, openpnp-top-view");
        id(input,"id");
        for(JsonElement e:array(input,"parts",10000)) {
            JsonObject item=e.getAsJsonObject();String id=id(item,"id"),packageId=id(item,"packageId");double height=num(item,"heightMm",0.001,50);
            if(parts.containsKey(nativeKey(id)))fail("DUPLICATE_ID","Duplicate part: "+id);
            org.openpnp.model.Package pkg=config.getPackage(packageId);
            if(pkg==null)fail("PACKAGE_UNMAPPED","Map package to an existing native package before import: "+packageId);
            Part part=existingParts==null?config.getPart(id):existingParts.part(id);
            if(part!=null) {
                if(part.getPackage()!=pkg||Math.abs(part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue()-height)>1e-6)fail("PART_CONFLICT","Existing native part differs from import: "+id);
            }
            parts.put(nativeKey(id),new PartSpec(id,item.has("value")&&!item.get("value").isJsonNull()?str(item,"value"):id,pkg,height,part));
        }
        for(JsonElement e:array(input,"boards",100)) {
            JsonObject item=e.getAsJsonObject();String id=id(item,"id");if(boards.containsKey(id))fail("DUPLICATE_ID","Duplicate board: "+id);
            Board board=new Board();board.setName(id);board.setDimensions(new Location(LengthUnit.Millimeters,num(item,"widthMm",0.001,500),num(item,"heightMm",0.001,500),0,0));
            Set<String> refs=new HashSet<>();
            for(JsonElement pe:array(item,"placements",10000)) {
                if(++placementCount>10000)fail("INPUT_TOO_LARGE","At most 10000 definition placements are supported");
                JsonObject p=pe.getAsJsonObject();String ref=id(p,"ref");if(!refs.add(ref))fail("DUPLICATE_REFERENCE","Duplicate reference: "+ref);
                PartSpec part=parts.get(nativeKey(id(p,"partId")));if(part==null)fail("PART_UNMAPPED","Missing canonical part for "+ref);
                if(part.pkg!=config.getPackage(id(p,"packageId"))||Math.abs(part.height-num(p,"heightMm",0.001,50))>1e-6)fail("PART_CONFLICT","Placement disagrees with part definition: "+ref);
                Placement placement=new Placement(ref);bindings.put(placement,part);placement.setPart(part.model);placement.setLocation(pose(p));placement.setSide(side(p));placement.setEnabled(bool(p,"enabled"));
                String type=str(p,"type");if(!Arrays.asList("placement","fiducial").contains(type))fail("INVALID_TYPE","Unknown placement type");
                placement.setType("fiducial".equals(type)?Placement.Type.Fiducial:Placement.Type.Placement);board.addPlacement(placement);
                if(placement.getLocation().getX()<0||placement.getLocation().getY()<0||placement.getLocation().getX()>board.getDimensions().getX()||placement.getLocation().getY()>board.getDimensions().getY())fail("OUTSIDE_BOARD","Placement outside board: "+ref);
            }
            boards.put(id,board);
        }
        for(JsonElement e:array(input,"panels",100)){JsonObject panel=e.getAsJsonObject();String id=id(panel,"id");if(panels.put(id,panel)!=null)fail("DUPLICATE_ID","Duplicate panel: "+id);}
        Job job=new Job();Set<String> siblingIds=new HashSet<>();
        for(JsonElement e:array(input,"instances",100)){JsonObject i=e.getAsJsonObject();if(!siblingIds.add(id(i,"id")))fail("DUPLICATE_ID","Duplicate root instance");job.addBoardOrPanelLocation(instance(i,new HashSet<>(),0));}
        if(job.getBoardLocations().isEmpty())fail("EMPTY_JOB","No board instances in job");
        PanelLocation.setParentsOfAllDescendants(job.getRootPanelLocation());
        return job;
    }
    private PlacementsHolderLocation<?> instance(JsonObject item,Set<String> ancestors,int depth)throws Exception {
        if(depth>8||++instanceCount>1000)fail("INPUT_TOO_LARGE","Instance graph exceeds native import limits");
        String kind=str(item,"kind"),definition=id(item,"definitionId");PlacementsHolderLocation<?> location;
        if("board".equals(kind)) {
            Board board=boards.get(definition);if(board==null)throw new Bridge.Fault("MISSING_DEFINITION","Unknown board "+definition);
            expandedPlacementCount+=board.getPlacements().size();if(expandedPlacementCount>10000)fail("INPUT_TOO_LARGE","Expanded placement count exceeds 10000");
            location=new BoardLocation(new Board(board));
        } else if("panel".equals(kind)) {
            JsonObject definitionObject=panels.get(definition);if(definitionObject==null)throw new Bridge.Fault("MISSING_DEFINITION","Unknown panel "+definition);
            if(ancestors.contains(definition))throw new Bridge.Fault("PANEL_CYCLE","Panel definitions contain a cycle");
            Set<String> next=new HashSet<>(ancestors);next.add(definition);
            Panel panel=new Panel();panel.setName(definition);panel.setDimensions(new Location(LengthUnit.Millimeters,num(definitionObject,"widthMm",0.001,500),num(definitionObject,"heightMm",0.001,500),0,0));
            PanelLocation panelLocation=new PanelLocation(panel);Set<String> siblings=new HashSet<>();
            for(JsonElement child:array(definitionObject,"children",100)){JsonObject c=child.getAsJsonObject();if(!siblings.add(id(c,"id")))fail("DUPLICATE_ID","Duplicate child instance");panelLocation.addChild(instance(c,next,depth+1));}
            location=panelLocation;
        } else throw new Bridge.Fault("INVALID_INSTANCE","Instance must be board or panel");
        location.setId(id(item,"id"));location.setLocation(pose(item));location.setSide(side(item));location.setLocallyEnabled(bool(item,"enabled"));location.setCheckFiducials(false);
        return location;
    }
    private static Location pose(JsonObject p)throws Exception{return new Location(LengthUnit.Millimeters,num(p,"x",-500,500),num(p,"y",-500,500),num(p,"z",-100,100),num(p,"rotation",-360,360));}
    private static Side side(JsonObject p)throws Exception{String s=str(p,"side");if(!Arrays.asList("top","bottom").contains(s))fail("INVALID_SIDE","Expected top or bottom");return "top".equals(s)?Side.Top:Side.Bottom;}
    private static String id(JsonObject p,String key)throws Exception{String value=str(p,key);if(!value.matches("[A-Za-z0-9_.:+-]{1,128}"))fail("INVALID_ID","Unsupported identifier: "+key);return value;}
    private static String str(JsonObject p,String key)throws Exception{if(!p.has(key)||!p.get(key).isJsonPrimitive()||!p.getAsJsonPrimitive(key).isString()||p.get(key).getAsString().length()>1024)fail("INVALID_ARGUMENT","Expected string: "+key);return p.get(key).getAsString();}
    private static double num(JsonObject p,String key,double min,double max)throws Exception{if(!p.has(key)||!p.get(key).isJsonPrimitive()||!p.getAsJsonPrimitive(key).isNumber())fail("INVALID_ARGUMENT","Expected number: "+key);double v=p.get(key).getAsDouble();if(!Double.isFinite(v)||v<min||v>max)fail("OUT_OF_RANGE","Out of range: "+key);return v;}
    private static boolean bool(JsonObject p,String key)throws Exception{if(!p.has(key)||!p.get(key).isJsonPrimitive()||!p.getAsJsonPrimitive(key).isBoolean())fail("INVALID_ARGUMENT","Expected boolean: "+key);return p.get(key).getAsBoolean();}
    private static JsonArray array(JsonObject p,String key,int max)throws Exception{if(!p.has(key)||!p.get(key).isJsonArray()||p.getAsJsonArray(key).size()>max)fail("INVALID_ARRAY","Expected bounded array: "+key);return p.getAsJsonArray(key);}
    private static void fail(String code,String message)throws Bridge.Fault{throw new Bridge.Fault(code,message);}
}
