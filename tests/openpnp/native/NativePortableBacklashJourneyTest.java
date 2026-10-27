/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.openpnp.model.*;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.spi.*;

/** Fresh-JVM portable adoption must retain typed backlash and the native planner's resulting segments. */
public final class NativePortableBacklashJourneyTest {
    static final Gson G=new Gson();static int checks;
    static void check(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
    static ReferenceControllerAxis axis(Configuration c,Axis.Type type){
        List<ReferenceControllerAxis> matches=new ArrayList<>();
        for(Axis a:c.getMachine().getAxes())if(a.getClass()==ReferenceControllerAxis.class&&a.getType()==type)matches.add((ReferenceControllerAxis)a);
        check(matches.size()==1,"exact single native "+type+" axis");return matches.get(0);
    }
    static JsonObject change(ReferenceControllerAxis a,String method,double offset,double speed,double sneak,double tolerance){return G.toJsonTree(Bridge.map("type",NativeAxisBacklashSettings.TYPE,"axis_id",a.getId(),"method",method,"offset_mm",offset,"speed_factor",speed,"sneak_up_mm",sneak,"acceptable_tolerance_mm",tolerance)).getAsJsonObject();}
    static double mm(Length length){return length.convertToUnits(LengthUnit.Millimeters).getValue();}
    static void near(double actual,double expected,String message){check(Double.isFinite(actual)&&Math.abs(actual-expected)<1e-12,message);}
    static void values(Configuration c){
        ReferenceControllerAxis x=axis(c,Axis.Type.X),y=axis(c,Axis.Type.Y);
        check(x.getBacklashCompensationMethod()==ReferenceControllerAxis.BacklashCompensationMethod.DirectionalSneakUp,"X method preserved");
        check(y.getBacklashCompensationMethod()==ReferenceControllerAxis.BacklashCompensationMethod.OneSidedPositioning,"Y method preserved");
        for(ReferenceControllerAxis a:List.of(x,y)){boolean isX=a==x;near(mm(a.getBacklashOffset()),isX?.2:-.1,"signed offset preserved");near(a.getBacklashSpeedFactor(),isX?.15:.35,"speed factor preserved");near(mm(a.getSneakUpOffset()),isX?.05:0,"sneak distance preserved");near(mm(a.getAcceptableTolerance()),isX?.017:.023,"acceptable tolerance preserved");}
    }
    static List<Map<String,Object>> segments(ReferenceControllerAxis axis,List<Motion> motions,List<Double> speeds){
        List<Map<String,Object>> rows=new ArrayList<>();check(motions.size()==speeds.size(),"every native segment has observed speed");
        for(int i=0;i<motions.size();i++)rows.add(Bridge.map("start",motions.get(i).getLocation0().getCoordinate(axis),"end",motions.get(i).getLocation1().getCoordinate(axis),"speed",speeds.get(i)));
        return rows;
    }
    static List<Map<String,Object>> observe(Configuration c)throws Exception {
        return c.getMachine().submit(()->{
            values(c);List<Map<String,Object>> result=new ArrayList<>();
            for(Axis.Type type:List.of(Axis.Type.X,Axis.Type.Y)){
                ReferenceControllerAxis a=axis(c,type);Map<String,Object> state=NativeAxisBacklashSettings.describe(c,a);
                check(Boolean.FALSE.equals(state.get("physical_calibration_valid"))&&Boolean.FALSE.equals(state.get("measurement_performed")),"readback grants no physical calibration");
                // Reuse the existing observer, never install it as the machine planner. This compares
                // fresh algorithm state on both sides, not physical/source in-flight backlash state.
                NativeAxisBacklashTest.axis=a;NativeAxisBacklashTest.tool=c.getMachine().getDefaultHead().getDefaultNozzle();
                List<Map<String,Object>> directions=new ArrayList<>();
                for(double requested:List.of(.8,.1)){
                    NativeAxisBacklashTest.Recorder recorder=new NativeAxisBacklashTest.Recorder();
                    List<Map<String,Object>> forward=segments(a,recorder.plan(0,10,requested),recorder.speeds);
                    List<Map<String,Object>> reverse=segments(a,recorder.plan(10,0,requested),recorder.speeds);
                    check(!forward.isEmpty()&&!reverse.isEmpty(),"actual inherited native planner produces both directions");
                    directions.add(Bridge.map("requested_speed",requested,"forward",forward,"reverse",reverse));
                }
                result.add(Bridge.map("axis_id",a.getId(),"axis_type",type.name(),"settings",state,"stored_getter_units",List.of(a.getBacklashOffset().getUnits().name(),a.getSneakUpOffset().getUnits().name(),a.getAcceptableTolerance().getUnits().name()),"native_segments",directions));
            }return result;
        },null,true).get(30,TimeUnit.SECONDS);
    }
    static void makeBoard(Configuration c,Path root)throws Exception {
        Board b=c.getBoard(root.resolve("saved-parity.board.xml").toFile());b.setName("Portable backlash parity");b.setDimensions(new Location(LengthUnit.Millimeters,20,10,0,0));
        Placement p=new Placement("R1");p.setPart(c.getPart("R0805-1K"));p.setSide(Abstract2DLocatable.Side.Top);p.setLocation(new Location(LengthUnit.Millimeters,2,3,0,0));b.addPlacement(p);
        Pad.RoundRectangle shape=new Pad.RoundRectangle();shape.setUnits(LengthUnit.Inches);shape.setWidth(.05);shape.setHeight(.04);shape.setRoundness(.375);BoardPad pad=new BoardPad(shape,new Location(LengthUnit.Inches,.1,.2,0,25));pad.setName("R1-1");b.addSolderPastePad(pad);c.saveBoard(b);
    }
    static void child(String phase,Path root)throws Exception {
        Configuration c=null;NativePortableLaunch launch=null;int code=0;
        try {
            if(phase.equals("source")){
                Path config=root.resolve("source-config");Configuration.initialize(config.toFile());c=Configuration.get();c.load();SimulatorMain.accelerateFixture(c);SimulatorMain.settleFreshFixture(config);c=Configuration.get();makeBoard(c,root);
                final Configuration selected=c;c.getMachine().submit(()->{JsonArray changes=new JsonArray();changes.add(change(axis(selected,Axis.Type.X),"DirectionalSneakUp",.2,.15,.05,.017));changes.add(change(axis(selected,Axis.Type.Y),"OneSidedPositioning",-.1,.35,0,.023));NativeSettings.stage(selected,changes).apply();return null;},null,true).get(30,TimeUnit.SECONDS);
                List<Map<String,Object>> before=observe(c);Files.writeString(root.resolve("source-observation.json"),G.toJson(before));Path board=c.getBoards().get(0).getFile().toPath();String original=NativePortableConfiguration.sha(Files.readAllBytes(board));
                Map<String,Object> exported=NativePortableConfiguration.export(c,root.resolve("portable.zip"));Files.writeString(root.resolve("archive-sha"),(String)exported.get("sha256"));check(original.equals(NativePortableConfiguration.sha(Files.readAllBytes(board))),"export preserves original board bytes");check(before.equals(observe(c)),"export preserves typed values and algorithm behavior");
            }else {
                launch=NativePortableLaunch.claim(root.resolve("adopted"),root.resolve("new-journal"));c=launch.initialize();
                // No settings apply, enable, home or job operation can mask a missing adopted value.
                check(!c.getMachine().isEnabled()&&!c.getMachine().isHomed(),"adopted first launch remains disabled/unhomed");check(c.getBoards().size()==1&&c.getBoards().get(0).getPlacements().size()==1&&c.getBoards().get(0).getSolderPastePads().size()==1,"native adopted board/placement/pad counts exact");
                check(G.toJsonTree(observe(c)).equals(new JsonParser().parse(Files.readString(root.resolve("source-observation.json")))),"fresh native adopted values and forward/reverse planner segments match source exactly");
                try(var files=Files.list(launch.journalDirectory)){check(files.findAny().isEmpty(),"no source execution journal or authority adopted");}
                Files.writeString(root.resolve("adopted-observation.json"),G.toJson(observe(c)));
            }
            check(!c.getMachine().isEnabled()&&!c.getMachine().isHomed(),"pure native qualification never enables or homes machine");check(NativePortableJourneyTest.feeds(c)==0,"pure planner observations perform no native feeds");
            System.out.println("OPENPNP_PORTABLE_BACKLASH_PHASE "+G.toJson(Bridge.map("phase",phase,"checks",checks,"native_placements",0,"physical_qualification",false)));
        }catch(Throwable failure){failure.printStackTrace();code=1;}finally{if(launch!=null)launch.close();else if(c!=null)c.getMachine().close();}System.exit(code);
    }
    public static void main(String[] args)throws Exception {
        if(args.length==2){child(args[0],Path.of(args[1]));return;}
        Path root=Files.createTempDirectory("native-portable-backlash-");
        for(String phase:List.of("source","adopt","target")){
            List<String> command=new ArrayList<>(List.of(Path.of(System.getProperty("java.home"),"bin/java").toString(),"-Xmx2g","-XX:+ExitOnOutOfMemoryError","-Djava.awt.headless=true","-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory","-Duser.home="+root,"-Djava.io.tmpdir="+root,"--add-opens=java.base/java.lang=ALL-UNNAMED","--add-opens=java.desktop/java.awt=ALL-UNNAMED","--add-opens=java.desktop/java.awt.color=ALL-UNNAMED","-cp",System.getProperty("java.class.path")));
            if(phase.equals("adopt"))command.addAll(List.of(NativePortableConfigurationMain.class.getName(),"--bundle",root.resolve("portable.zip").toString(),"--sha256",Files.readString(root.resolve("archive-sha")),"--destination",root.resolve("adopted").toString()));else command.addAll(List.of(NativePortableBacklashJourneyTest.class.getName(),phase,root.toString()));
            Process child=new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(root.resolve(phase+".log").toFile()).start();if(!child.waitFor(60,TimeUnit.SECONDS)){child.destroyForcibly();child.waitFor(5,TimeUnit.SECONDS);throw new AssertionError("Portable backlash phase timeout "+phase);}check(child.exitValue()==0,"Portable backlash phase "+phase+" failed: "+Files.readString(root.resolve(phase+".log")));
        }
        System.out.println("OPENPNP_PORTABLE_BACKLASH_RESULT "+G.toJson(Bridge.map("phases",3,"axes",2,"native_placements",0,"physical_qualification",false,"root",root.toString())));
    }
}
