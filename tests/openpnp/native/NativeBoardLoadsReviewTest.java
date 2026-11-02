/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.awt.geom.AffineTransform;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.model.Abstract2DLocatable.Side;

/** Independent native model and durable-record boundary checks. No machine effects or GUI. */
public final class NativeBoardLoadsReviewTest {
    static final Gson JSON=new Gson();
    static final List<String> passed=new ArrayList<>();
    static final Map<String,String> failed=new LinkedHashMap<>();
    static Path root;static int assertions;
    interface Checked {void run()throws Exception;}
    static void check(boolean value,String message){assertions++;if(!value)throw new AssertionError(message);}
    static void group(String name,Checked action){try{action.run();passed.add(name);}catch(Throwable failure){failed.put(name,failure.toString());failure.printStackTrace();}}
    static void reject(String code,Checked action)throws Exception{
        try{action.run();throw new AssertionError("Expected refusal "+code);}catch(Bridge.Fault failure){check(code==null||code.equals(failure.code),"Expected "+code+", got "+failure.code);}
    }
    static Location pose(double x,double y){return new Location(LengthUnit.Millimeters,x,y,0,0);}
    static BoardLocation board(String id,String reference){
        Board board=new Board();board.setDimensions(pose(20,20));
        Placement p=new Placement(reference);p.setLocation(pose(3,4));board.addPlacement(p);
        BoardLocation location=new BoardLocation(board);location.setId(id);location.setLocation(pose(10,10));return location;
    }
    static Job twoBoards(){Job job=new Job();job.addBoardOrPanelLocation(board("A","R1"));job.addBoardOrPanelLocation(board("B","R1"));PanelLocation.setParentsOfAllDescendants(job.getRootPanelLocation());return job;}
    static Job panel()throws Exception{
        Panel panel=new Panel();panel.setDimensions(pose(60,40));PanelLocation location=new PanelLocation(panel);location.setId("P");
        BoardLocation a=board("A","FID");a.getBoard().getPlacements().get("FID").setType(Placement.Type.Fiducial);location.addChild(a);location.addChild(board("B","R1"));
        panel.addPseudoPlacement(panel.createPseudoPlacement("A⇒FID"));Job job=new Job();job.addBoardOrPanelLocation(location);PanelLocation.setParentsOfAllDescendants(job.getRootPanelLocation());return job;
    }
    static final class Recorder implements AutoCloseable {
        final Path path;final FileChannel channel;final NativeBoardLoads loads;long sequence;
        Recorder(String name)throws Exception{path=root.resolve(name+".jsonl");channel=FileChannel.open(path,StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE);loads=new NativeBoardLoads(this::append);}
        void append(String type,Map<String,Object> payload)throws Exception{
            Map<String,Object> envelope=new LinkedHashMap<>();envelope.put("sequence",++sequence);envelope.put("type",type);envelope.put("payload",payload);
            ByteBuffer bytes=ByteBuffer.wrap((JSON.toJson(envelope)+"\n").getBytes(StandardCharsets.UTF_8));while(bytes.hasRemaining())channel.write(bytes);channel.force(true);
        }
        long size()throws Exception{return Files.size(path);}
        NativeBoardLoads replay()throws Exception{NativeBoardLoads recovered=new NativeBoardLoads(this::append);for(String line:Files.readAllLines(path)){Map<String,Object> event=JSON.fromJson(line,Map.class);recovered.recoverEvent((String)event.get("type"),(Map<String,Object>)event.get("payload"));}recovered.finishRecovery();return recovered;}
        public void close()throws Exception{channel.close();}
    }
    static String loadId(NativeBoardLoads loads,String nativeId){for(Object raw:(List<?>)loads.snapshot().get("roots")){Map<?,?> row=(Map<?,?>)raw;if(nativeId.equals(row.get("root_instance_id")))return(String)row.get("load_id");}throw new AssertionError(nativeId);}
    static void duplicateIds()throws Exception{
        Job job=panel();PanelLocation p=(PanelLocation)job.getRootPanelLocation().getChildren().get(0);p.getChildren().get(1).setId("A");
        check(p.getChildren().get(0).getUniqueId().equals(p.getChildren().get(1).getUniqueId()),"Fixture has two actual native children sharing a full ID");
        try(Recorder r=new Recorder("duplicate-native-ids")){
            reject("BOARD_LOAD_ID_CONFLICT",()->r.loads.bindJob(job,"duplicate-job",true));
            check(r.size()==0&&r.loads.revision().equals("load-0"),"Invalid graph leaves durable journal and revision unchanged");
            r.replay();check(r.size()==0,"Refused graph leaves a reopenable empty journal");
        }
    }
    static void historyAlias()throws Exception{
        Job job=new Job();BoardLocation a=board("A","B⇒R1"),b=board("A⇒B","R1");job.addBoardOrPanelLocation(a);job.addBoardOrPanelLocation(b);
        job.storePlacedStatus(a,"B⇒R1",true);check(job.retrievePlacedStatus(b,"R1"),"Actual native history concatenation aliases distinct board/reference pairs");
        try(Recorder r=new Recorder("native-history-key-alias")){
            reject("BOARD_LOAD_ID_CONFLICT",()->r.loads.bindJob(job,"alias-job",true));
            check(r.size()==0&&job.retrievePlacedStatus(a,"B⇒R1")&&job.retrievePlacedStatus(b,"R1"),"History alias refusal writes nothing and preserves both observed statuses");
        }
    }
    static void mismatchedNativeParent()throws Exception{
        Job job=panel();PanelLocation p=(PanelLocation)job.getRootPanelLocation().getChildren().get(0);
        BoardLocation child=(BoardLocation)p.getChildren().get(0);PanelLocation detached=new PanelLocation(new Panel());detached.setId("Q");child.setParent(detached);
        check(p.getChildren().contains(child)&&child.getUniqueId().startsWith("Q⇒"),"Actual native child enumeration and parent-derived history identity disagree");
        try(Recorder r=new Recorder("wrong-native-parent")){
            reject("BOARD_LOAD_ID_CONFLICT",()->r.loads.bindJob(job,"wrong-parent-job",true));
            check(r.size()==0&&r.loads.revision().equals("load-0"),"Detached parent identity cannot publish a root-scoped load record");
        }
    }
    static void pseudoMutation(int variant)throws Exception{
        Job job=panel();PanelLocation panel=(PanelLocation)job.getRootPanelLocation().getChildren().get(0);Placement pseudo=panel.getPanel().getPseudoPlacements().get(0);
        try(Recorder r=new Recorder("pseudo-change-"+variant)){
            r.loads.bindJob(job,"pseudo-job",true);String signature=r.loads.jobRevision(),load=loadId(r.loads,"P");long before=r.size();
            if(variant==0)pseudo.setLocation(pseudo.getLocation().add(pose(7,0)));
            if(variant==1)pseudo.setEnabled(!pseudo.isEnabled());
            if(variant==2)pseudo.setType(Placement.Type.Placement);
            check(pseudo.getId().equals("A⇒FID"),"Pseudo edit retains native ID");
            reject("BOARD_LOAD_JOB_MISMATCH",()->r.loads.requireReady(job));
            reject("BOARD_LOAD_JOB_MISMATCH",()->r.loads.change(job,"P","same-load","top",load,r.loads.revision(),false));
            check(r.size()==before,"Same-ID changed pseudo metadata cannot publish a new binding");
            r.loads.jobChanged(job,"changed-job");check(!signature.equals(r.loads.jobRevision()),"Changed pseudo metadata changes the native job revision");
        }
    }
    static void inlineRootMutation(int variant)throws Exception{
        Job job=twoBoards();PanelLocation virtual=job.getRootPanelLocation();
        try(Recorder r=new Recorder("inline-root-change-"+variant)){
            r.loads.bindJob(job,"root-job",true);String load=loadId(r.loads,"A");long before=r.size();
            if(variant==0)virtual.setLocation(pose(15,0));
            if(variant==1)virtual.setSide(Side.Bottom);
            if(variant==2)virtual.setLocallyEnabled(false);
            if(variant==3)virtual.setCheckFiducials(true);
            if(variant==4)virtual.setLocalToGlobalTransform(AffineTransform.getTranslateInstance(8,4));
            reject(null,()->r.loads.requireReady(job));
            reject(null,()->r.loads.change(job,"A","same-load",job.getBoardLocations().get(0).getGlobalSide().name().toLowerCase(Locale.ROOT),load,r.loads.revision(),false));
            check(r.size()==before,"Changed implicit job root cannot publish current load authority");
        }
    }
    static Map<?,?> retained(NativeBoardLoads loads,String id){for(Object raw:(List<?>)loads.snapshot().get("loads")){Map<?,?> row=(Map<?,?>)raw;if(id.equals(row.get("load_id")))return row;}throw new AssertionError(id);}
    static void opaqueHistory()throws Exception{
        Job job=twoBoards();BoardLocation a=job.getBoardLocations().get(0),b=job.getBoardLocations().get(1);
        try(Recorder r=new Recorder("opaque-history-replacement")){
            r.loads.bindJob(job,"opaque-job",true);String oldA=loadId(r.loads,"A"),oldB=loadId(r.loads,"B");
            job.storePlacedStatus(a,"R1",true);job.storePlacedStatus(a,"OLD_REMOVED",true);
            job.storePlacedStatus(b,"R1",true);job.storePlacedStatus(b,"OLD_REMOVED",true);
            r.loads.checkpoint(job);r.loads.change(job,"A","replace","top",oldA,r.loads.revision(),false);
            a.getBoard().addPlacement(new Placement("OLD_REMOVED"));
            check(!job.retrievePlacedStatus(a,"OLD_REMOVED"),"Replacement removes the selected root's opaque removed-reference history before that ID reappears");
            check(!job.retrievePlacedStatus(a,"R1")&&job.retrievePlacedStatus(b,"R1")&&job.retrievePlacedStatus(b,"OLD_REMOVED"),"Replacement preserves other-root current and opaque history exactly");
            check(!loadId(r.loads,"A").equals(oldA)&&loadId(r.loads,"B").equals(oldB),"Replacement changes only the selected root's load identity");
            check(((Number)retained(r.loads,oldA).get("placed_history_count")).intValue()==2,"Retired load keeps both known and removed-reference history");
            NativeBoardLoads replay=r.replay();check(((Number)retained(replay,oldA).get("placed_history_count")).intValue()==2,"Complete journal replay preserves retired opaque history");
        }
    }
    static void regressingCheckpoint(boolean retainFalseKey)throws Exception{
        Job job=twoBoards();BoardLocation a=job.getBoardLocations().get(0);
        try(Recorder r=new Recorder("regressing-history-checkpoint-"+retainFalseKey)){
            r.loads.bindJob(job,"history-job",true);String id=loadId(r.loads,"A");job.storePlacedStatus(a,"R1",true);r.loads.checkpoint(job);
            long size=r.size();String revision=r.loads.revision();if(retainFalseKey)job.storePlacedStatus(a,"R1",false);else job.removePlacedStatus(a,"R1");
            boolean refused=false;try{r.loads.checkpoint(job);}catch(Exception expected){refused=true;}
            check(refused,"Regressing native history checkpoint is refused");
            check(r.size()==size&&r.loads.revision().equals(revision),"Regressing history fails before durable bytes or revision change");
            NativeBoardLoads replay=r.replay();check(((Number)retained(replay,id).get("placed_history_count")).intValue()==1,"Refused regression leaves complete journal replay valid and prior history intact");
        }
    }
    static void opaqueCapacityReservation()throws Exception{
        Job job=new Job();BoardLocation a=board("A","R1");job.addBoardOrPanelLocation(a);
        try(Recorder r=new Recorder("opaque-history-capacity")){
            r.loads.bindJob(job,"capacity-job",true);String id=loadId(r.loads,"A");
            for(int i=0;i<NativeBoardLoads.MAX_HISTORY;i++)job.storePlacedStatus(a,"OLD_"+i,true);
            r.loads.checkpoint(job);long before=r.size();
            check(((Number)retained(r.loads,id).get("placed_history_count")).intValue()==NativeBoardLoads.MAX_HISTORY,"Fixture fills the actual durable native-history capacity with removed references");
            reject("BOARD_LOAD_CAPACITY",()->r.loads.requireReady(job));
            check(r.size()==before&&!job.retrievePlacedStatus(a,"R1"),"Readiness reserves retained opaque keys before any new placement or journal write");
        }
    }
    public static void main(String[] args)throws Exception{
        root=Files.createTempDirectory("openpnp-board-load-review-");Configuration.initialize(root.resolve("configuration").toFile());Configuration config=Configuration.get();config.load();
        try{
            group("duplicate expanded IDs are refused before the durable sink",NativeBoardLoadsReviewTest::duplicateIds);
            group("ambiguous native history keys are refused before reset",NativeBoardLoadsReviewTest::historyAlias);
            group("native parent links agree with the registered root",NativeBoardLoadsReviewTest::mismatchedNativeParent);
            for(int i=0;i<3;i++){final int n=i;group("pseudo metadata changes invalidate binding "+i,()->pseudoMutation(n));}
            for(int i=0;i<5;i++){final int n=i;group("inline root changes cannot reuse load authority "+i,()->inlineRootMutation(n));}
            group("replacement scopes opaque history and preserves the retired ledger",NativeBoardLoadsReviewTest::opaqueHistory);
            group("removed history cannot poison a durable checkpoint",()->regressingCheckpoint(false));
            group("true-to-false history cannot erase a completed record",()->regressingCheckpoint(true));
            group("opaque retained history remains in placement-capacity reservation",NativeBoardLoadsReviewTest::opaqueCapacityReservation);
            Map<String,Object> result=new LinkedHashMap<>();result.put("passed",passed);result.put("failed",failed);result.put("assertions",assertions);result.put("native_model_only",true);result.put("native_motion_effects",0);result.put("native_feed_effects",0);result.put("physical_loading_verified",false);result.put("process_crash_test",false);result.put("fixture_directory",root.toString());
            System.out.println("OPENPNP_NATIVE_BOARD_LOAD_REVIEW_RESULT "+JSON.toJson(result));
        }finally{config.getMachine().close();}
        System.exit(failed.isEmpty()?0:1);
    }
}
