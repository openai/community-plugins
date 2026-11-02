/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.model.Abstract2DLocatable.Side;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.feeder.*;

/** Exercises pinned native configuration, sample job and model APIs without enabling or moving. */
public final class NativeJobPreflightTest {
    static final Gson GSON=new Gson();static final List<String> passed=new ArrayList<>();
    static Configuration config;static Machine machine;static ReferenceNozzle nozzle;static ReferenceNozzleTip tip;
    static Part part;static org.openpnp.model.Package pkg;static ReferenceTrayFeeder tray;
    static void check(boolean ok,String message){if(!ok)throw new AssertionError(message);}
    static JsonObject review(Job job){return GSON.toJsonTree(NativeJobPreflight.validate(config,job)).getAsJsonObject();}
    static boolean has(JsonObject result,String list,String code){for(JsonElement e:result.getAsJsonArray(list))if(e.getAsJsonObject().get("code").getAsString().equals(code))return true;return false;}
    static int count(JsonObject result,String key){return result.getAsJsonObject("counts").get(key).getAsInt();}
    static Placement placement(String id,Part value){Placement p=new Placement(id);p.setPart(value);p.setSide(Side.Top);p.setEnabled(true);p.setLocation(new Location(LengthUnit.Millimeters,5,5,0,0));return p;}
    static BoardLocation board(String id,Placement... placements){Board b=new Board();b.setName(id);for(Placement p:placements)b.addPlacement(p);BoardLocation l=new BoardLocation(b);l.setId(id);l.setSide(Side.Top);return l;}
    static Job job(BoardLocation board){Job job=new Job();job.addBoardOrPanelLocation(board);return job;}
    static ReferenceTrayFeeder tray(int capacity,int used)throws Exception{ReferenceTrayFeeder f=new ReferenceTrayFeeder();f.setPart(part);f.setEnabled(true);f.setTrayCountX(capacity);f.setTrayCountY(1);f.setOffsets(new Location(LengthUnit.Millimeters,4,0,0,0));f.setFeedCount(used);machine.addFeeder(f);return f;}

    public static void main(String[] args)throws Exception {
        Path root=Files.createTempDirectory("openpnp-native-preflight-");Configuration.initialize(root.toFile());config=Configuration.get();config.load();machine=config.getMachine();
        nozzle=(ReferenceNozzle)machine.getDefaultHead().getDefaultNozzle();tip=(ReferenceNozzleTip)nozzle.getNozzleTip();
        int exit=0;
        try {
            Path sampleRoot=args.length==0?Paths.get(System.getProperty("user.home"),".cache","openpnp-codex","upstream","samples"):Paths.get(args[0]);
            Job sample=config.loadJob(Bridge.copySample(sampleRoot,root).resolve("pnp-test.job.xml").toFile());
            JsonObject initial=review(sample);check(initial.get("valid").getAsBoolean(),"native configured sample should validate: "+initial);
            check(count(initial,"pending")>0&&has(initial,"warnings","CAPACITY_UNKNOWN"),"default strip maximum zero is unknown material, not a fabricated count");
            check(!machine.isEnabled()&&!machine.isHomed(),"read-only preflight does not start the native simulator");
            passed.add("actual native sample remains valid with explicit unknown-strip-capacity warning and no enabling");

            pkg=new org.openpnp.model.Package("preflight-package");pkg.addCompatibleNozzleTip(tip);config.addPackage(pkg);
            part=new Part("preflight-part");part.setPackage(pkg);part.setHeight(new Length(0.5,LengthUnit.Millimeters));config.addPart(part);
            tray=tray(2,0);
            testCapacitiesAndHistory();testExclusions();testTooling();testPartFailures();
            check(!machine.isEnabled()&&!machine.isHomed(),"all preflight checks preserve disabled and unhomed native state");
            System.out.println("OPENPNP_NATIVE_PREFLIGHT_RESULT "+GSON.toJson(Bridge.map("passed",passed,"upstream_commit",Bridge.UPSTREAM,"simulation_only",true,"physical_qualification",false)));
        }catch(Throwable e){e.printStackTrace();exit=1;}finally{machine.close();}System.exit(exit);
    }
    static void testCapacitiesAndHistory() throws Exception {
        BoardLocation b=board("stock-board",placement("R1",part),placement("R2",part),placement("R3",part));Job job=job(b);
        ReferenceTrayFeeder disabled=tray(20,0);disabled.setEnabled(false);
        JsonObject shortfall=review(job);check(!shortfall.get("valid").getAsBoolean()&&has(shortfall,"errors","INSUFFICIENT_CONFIGURED_CAPACITY"),"disabled feeder capacity must not cover shortfall");
        ReferenceStripFeeder strip=new ReferenceStripFeeder();strip.setPart(part);strip.setEnabled(true);strip.setVisionEnabled(false);strip.setMaxFeedCount(5);strip.setFeedCount(4);machine.addFeeder(strip);
        JsonObject enough=review(job);check(enough.get("valid").getAsBoolean(),"aggregate finite tray2+strip1 covers three pending placements");
        JsonObject material=enough.getAsJsonArray("material_requirements").get(0).getAsJsonObject();check(material.get("known_remaining_configured_feeds").getAsInt()==3,"remaining capacity uses native maximum minus consumed count");
        check(strip.getFeedCount()==4&&tray.getFeedCount()==0,"preflight never advances or resets native feeder counts");
        strip.setMaxFeedCount(0);JsonObject unknown=review(job);check(unknown.get("valid").getAsBoolean()&&has(unknown,"warnings","CAPACITY_NOT_PROVEN"),"unknown native strip supply is reported honestly without false shortage certainty");
        strip.setEnabled(false);job.storePlacedStatus(b,"R1",true);JsonObject pendingOnly=review(job);
        check(pendingOnly.get("valid").getAsBoolean()&&count(pendingOnly,"pending")==2&&count(pendingOnly,"placed")==1,"placed history removes material demand from pending work");
        check(job.retrievePlacedStatus(b,"R1"),"validation preserves placed record");
        tray.setFeedOptions(ReferenceFeeder.FeedOptions.SkipNext);check(has(review(job),"warnings","CAPACITY_UNKNOWN"),"non-normal native feed mode cannot support a normal consumption estimate");tray.setFeedOptions(ReferenceFeeder.FeedOptions.Normal);
        machine.removeFeeder(strip);machine.removeFeeder(disabled);
        passed.add("finite feeder capacity aggregation, disabled stock exclusion, unknown supply and placed-history demand");
    }
    static void testExclusions() {
        Placement done=placement("done",null),dnp=placement("dnp",null),bottom=placement("bottom",null),fid=placement("fid",null);
        dnp.setEnabled(false);bottom.setSide(Side.Bottom);fid.setType(Placement.Type.Fiducial);
        BoardLocation b=board("filters",placement("pending",part),done,dnp,bottom,fid);Job job=job(b);job.storePlacedStatus(b,"done",true);
        PanelLocation panel=new PanelLocation(new Panel());panel.setId("X-out-panel");panel.setLocallyEnabled(false);BoardLocation excluded=board("X-out-child",placement("bad",null));panel.addChild(excluded);job.addBoardOrPanelLocation(panel);
        check(excluded.isLocallyEnabled()&&!excluded.isEnabled(),"native ancestor X-out fixture propagates enable state");
        JsonObject result=review(job);check(result.get("valid").getAsBoolean(),"excluded or completed placements do not generate missing-part errors");
        check(count(result,"placement_records_total")==6&&count(result,"pending")==1&&count(result,"placed")==1&&count(result,"excluded")==4,"pending/placed/excluded partition is exact");
        check(job.retrievePlacedStatus(b,"done"),"excluded checks preserve stored placed status");
        PanelLocation flipped=new PanelLocation(new Panel());flipped.setId("bottom-panel");flipped.setSide(Side.Bottom);
        Placement selected=placement("selected-bottom",part);selected.setSide(Side.Bottom);
        BoardLocation child=board("nested-flip",selected,placement("other-top",null));flipped.addChild(child);Job flippedJob=new Job();flippedJob.addBoardOrPanelLocation(flipped);
        check(child.getGlobalSide()==Side.Bottom,"native parent side flip fixture");
        JsonObject flip=review(flippedJob);check(flip.get("valid").getAsBoolean()&&count(flip,"pending")==1&&count(flip,"excluded")==1,"native global side controls pending placements below a flipped panel");
        passed.add("ancestor X-outs, nested side flips, DNP/fiducial exclusions and placed-history preservation");
    }
    static void testTooling() throws Exception {
        Job job=job(board("tooling",placement("R1",part)));
        ReferenceNozzleTip spare=new ReferenceNozzleTip();machine.addNozzleTip(spare);nozzle.addCompatibleNozzleTip(spare);
        pkg.removeCompatibleNozzleTip(tip);pkg.addCompatibleNozzleTip(spare);boolean changer=nozzle.isChangerEnabled();nozzle.setChangerEnabled(false);
        JsonObject manual=review(job);check(has(manual,"errors","MANUAL_TIP_CHANGE_REQUIRED"),"unmounted compatible tip on manual changer blocks unattended readiness");
        nozzle.setChangerEnabled(true);check(review(job).get("valid").getAsBoolean(),"native automatic changer provides an available compatible option");
        pkg.removeCompatibleNozzleTip(spare);check(has(review(job),"errors","NO_COMPATIBLE_LOADABLE_TIP"),"package compatibility intersects actual nozzle compatibility");
        pkg.addCompatibleNozzleTip(tip);nozzle.setChangerEnabled(changer);nozzle.removeCompatibleNozzleTip(spare);machine.removeNozzleTip(spare);
        boolean enabled=tip.getCalibration().isEnabled();tip.getCalibration().setEnabled(true);tip.getCalibration().resetAll();
        JsonObject calibration=review(job);check(calibration.get("valid").getAsBoolean()&&has(calibration,"warnings","NATIVE_CALIBRATION_WILL_BE_ATTEMPTED"),"enabled invalid runout model reports native processor calibration attempt honestly");
        check(!tip.getCalibration().isCalibrated(nozzle)&&nozzle.getNozzleTip()==tip,"preflight performs neither calibration nor tip change");tip.getCalibration().setEnabled(enabled);
        passed.add("native loadable-tip compatibility, manual/automatic changer distinction and unexecuted calibration plan");
    }
    static void testPartFailures() {
        Job job=job(board("definitions",placement("R1",part)));
        part.setHeight(new Length(Double.NaN,LengthUnit.Millimeters));check(has(review(job),"errors","UNKNOWN_PART_HEIGHT"),"nonfinite native height rejected");part.setHeight(new Length(0.5,LengthUnit.Millimeters));
        part.setPackage(null);check(has(review(job),"errors","MISSING_PACKAGE"),"missing package rejected");part.setPackage(pkg);
        tray.setEnabled(false);check(has(review(job),"errors","NO_ENABLED_FEEDER"),"missing enabled material route rejected");tray.setEnabled(true);
        check(has(review(job(board("missing",placement("R1",null)))),"errors","MISSING_PART"),"missing pending part rejected");
        passed.add("pending part/package/finite height and enabled material route checks");
    }
}
