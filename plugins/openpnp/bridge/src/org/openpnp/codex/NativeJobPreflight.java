/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.util.*;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.feeder.*;

/**
 * Read-only, bounded preflight for the trusted native simulator. Mirrors the native processor's
 * pending-placement filters and getCompatibleNozzleTips(part), without invoking its PreFlight step
 * (which moves to safe Z, discards parts, prepares feeders and runs scripts). Counter availability
 * is a configured model estimate, never an inventory measurement or a retry allowance.
 */
public final class NativeJobPreflight {
    private NativeJobPreflight() { }
    private static final int MAX_RECORDS=100000, MAX_DIAGNOSTICS=200;

    public static Map<String,Object> validate(Configuration config,Job job) {
        Review review=new Review();
        if(config==null||config.getMachine()==null||job==null){review.error("NO_JOB","A loaded native configuration and job are required",null);return review.result();}
        Machine machine=config.getMachine();Head head;
        try{head=machine.getDefaultHead();}catch(Exception e){review.error("NO_DEFAULT_HEAD","The native job processor needs a default head",null);return review.result();}
        Map<Part,Integer> required=new LinkedHashMap<>();
        for(BoardLocation board:job.getBoardLocations()) {
            if(board.getBoard()==null){review.error("MISSING_BOARD","Board instance has no native board",board.getUniqueId());continue;}
            Set<String> pendingRefs=new HashSet<>();
            for(Placement placement:board.getBoard().getPlacements()) {
                if(++review.total>MAX_RECORDS){review.error("JOB_LIMIT","Preflight is limited to 100000 placement records",null);return review.result();}
                // isEnabled includes the entire native parent chain; getGlobalSide includes panel flips.
                if(placement.getType()!=Placement.Type.Placement){review.exclude("not-placement");continue;}
                if(job.retrievePlacedStatus(board,placement.getId())){review.placed++;continue;}
                if(!board.isEnabled()){review.exclude("board-or-ancestor-disabled");continue;}
                if(!placement.isEnabled()){review.exclude("placement-disabled");continue;}
                if(placement.getSide()!=board.getGlobalSide()){review.exclude("other-side");continue;}
                review.pending++;
                String ref=board.getUniqueId()+"/"+placement.getId();
                if(!pendingRefs.add(placement.getId()))review.error("DUPLICATE_PENDING_REFERENCE","Duplicate reference among pending placements",ref);
                Part part=placement.getPart();
                if(part==null){review.error("MISSING_PART","Pending placement has no native part",ref);continue;}
                required.put(part,required.getOrDefault(part,0)+1);
            }
        }
        for(Map.Entry<Part,Integer> entry:required.entrySet()) {
            Part part=entry.getKey();int needed=entry.getValue();
            if(part.getPackage()==null)review.error("MISSING_PACKAGE","Pending part has no package",part.getId());
            Length height=part.getHeight();double heightMm=height==null?Double.NaN:height.convertToUnits(LengthUnit.Millimeters).getValue();
            if(part.isPartHeightUnknown()||!Double.isFinite(heightMm)||heightMm<=0)review.error("UNKNOWN_PART_HEIGHT","Explicit finite positive part height is required; this preflight does not probe height",part.getId());
            validateTooling(machine,head,part,review);
            validateMaterial(machine,part,needed,review);
        }
        return review.result();
    }

    private static void validateTooling(Machine machine,Head head,Part part,Review review) {
        if(part.getPackage()==null)return;
        List<Map<String,Object>> options=new ArrayList<>();boolean compatible=false,ready=false;
        for(Nozzle nozzle:head.getNozzles()) {
            if(nozzle.getClass()!=ReferenceNozzle.class)continue;
            ReferenceNozzle n=(ReferenceNozzle)nozzle;
            for(NozzleTip raw:n.getCompatibleNozzleTips(part)) {
                compatible=true;
                if(raw.getClass()!=ReferenceNozzleTip.class)continue;
                ReferenceNozzleTip tip=(ReferenceNozzleTip)raw;
                boolean loaded=n.getNozzleTip()==tip,changer=n.isChangerEnabled();
                Nozzle owner=tip.getNozzleWhereLoaded();
                boolean ownerCanUnload=owner==null||owner==n||(owner.getClass()==ReferenceNozzle.class&&((ReferenceNozzle)owner).isChangerEnabled());
                boolean available=loaded||(changer&&ownerCanUnload);
                ready|=available;
                boolean needsCalibration=tip.getCalibration().isEnabled()&&!tip.getCalibration().isCalibrated(n);
                options.add(values("nozzle_id",n.getId(),"nozzle_tip_id",tip.getId(),"currently_loaded",loaded,"automatic_change_available",!loaded&&changer&&ownerCanUnload,"manual_change_required",!available,"runout_calibration_enabled",tip.getCalibration().isEnabled(),"runout_calibrated",tip.getCalibration().isCalibrated(n),"native_calibration_may_be_required",needsCalibration));
                if(available&&needsCalibration) {
                    if(machine.getPnpJobProcessor().getClass()==ReferencePnpJobProcessor.class)
                        review.warning("NATIVE_CALIBRATION_WILL_BE_ATTEMPTED","Native CalibrateNozzleTips attempts runout calibration before pick if this option is selected; calibration has not been executed or validated by preflight",part.getId(),values("nozzle_id",n.getId(),"nozzle_tip_id",tip.getId()));
                    else review.error("CALIBRATION_REQUIRED","Compatible tip has no current enabled runout model and processor calibration behavior is unqualified",part.getId());
                }
            }
        }
        if(!compatible)review.error("NO_COMPATIBLE_LOADABLE_TIP","No default-head native nozzle reports a compatible, loadable tip for this part",part.getId());
        else if(!ready)review.error("MANUAL_TIP_CHANGE_REQUIRED","Compatible tips require an operator change before this head can place the part",part.getId());
        review.tooling.add(values("part_id",part.getId(),"compatible_options",options,"ready_without_manual_change",ready));
    }

    private static void validateMaterial(Machine machine,Part part,int needed,Review review) {
        long known=0;int enabled=0,unknown=0;List<Map<String,Object>> sources=new ArrayList<>();
        for(Feeder feeder:machine.getFeeders()) {
            if(!feeder.isEnabled()||feeder.getPart()!=part)continue;
            enabled++;Long remaining=null;String basis="unsupported-feeder-capacity";Integer count=null;
            if(feeder.getClass()==ReferenceTrayFeeder.class) {
                ReferenceTrayFeeder f=(ReferenceTrayFeeder)feeder;count=f.getFeedCount();
                if(count<0)review.error("INVALID_FEED_COUNT","Native feeder count is negative",f.getId());
                else if(f.getFeedOptions()!=ReferenceFeeder.FeedOptions.Normal)basis="non-normal-feed-options";
                else {remaining=Math.max(0,(long)f.getEffectiveTrayCountX()*f.getEffectiveTrayCountY()-count);basis="native-effective-tray-count-minus-feed-count";}
            } else if(feeder.getClass()==ReferenceStripFeeder.class) {
                ReferenceStripFeeder f=(ReferenceStripFeeder)feeder;count=f.getFeedCount();
                if(count<0||f.getMaxFeedCount()<0)review.error("INVALID_FEED_COUNT","Native strip count or maximum is negative",f.getId());
                else if(f.getFeedOptions()!=ReferenceFeeder.FeedOptions.Normal)basis="non-normal-feed-options";
                else if(f.getMaxFeedCount()==0)basis="native-strip-maximum-unconfigured";
                else {remaining=Math.max(0,(long)f.getMaxFeedCount()-count);basis="native-strip-maximum-minus-feed-count";}
            }
            if(remaining==null){unknown++;review.warning("CAPACITY_UNKNOWN","Native feeder configuration does not establish remaining material; no inventory count is inferred",part.getId(),values("feeder_id",feeder.getId(),"reason",basis));}
            else try{known=Math.addExact(known,remaining);}catch(ArithmeticException e){review.error("CAPACITY_OVERFLOW","Configured aggregate capacity exceeds the supported integer range",part.getId());}
            sources.add(values("feeder_id",feeder.getId(),"native_class",feeder.getClass().getName(),"feed_count",count,"remaining_configured_feeds",remaining,"basis",basis));
        }
        if(enabled==0)review.error("NO_ENABLED_FEEDER","No enabled feeder is assigned to the pending native part identity",part.getId());
        else if(known<needed&&unknown==0)review.error("INSUFFICIENT_CONFIGURED_CAPACITY","Pending placements exceed the aggregate configured remaining feeds",part.getId(),values("required",needed,"known_remaining",known));
        else if(known<needed)review.warning("CAPACITY_NOT_PROVEN","Known remaining feeds do not cover pending placements and other enabled feeder capacity is unknown",part.getId(),values("required",needed,"known_remaining",known,"unknown_capacity_feeders",unknown));
        if(enabled>1)review.warning("MULTIPLE_FEEDER_ALLOCATION","Capacity sums enabled feeders for this native part; native feeder selection, failover and physical inventory have not been exercised",part.getId(),values("enabled_feeders",enabled));
        review.materials.add(values("part_id",part.getId(),"required_pending_placements",needed,"known_remaining_configured_feeds",known,"unknown_capacity_feeders",unknown,"sources",sources,"physical_inventory_verified",false,"retry_allowance_included",false));
    }

    private static final class Review {
        int total,pending,placed,excluded,errorCount,warningCount;
        final List<Map<String,Object>> errors=new ArrayList<>(),warnings=new ArrayList<>(),materials=new ArrayList<>(),tooling=new ArrayList<>();
        final Map<String,Integer> excludedReasons=new LinkedHashMap<>();
        void exclude(String reason){excluded++;excludedReasons.put(reason,excludedReasons.getOrDefault(reason,0)+1);}
        void error(String code,String message,String target){error(code,message,target,new LinkedHashMap<>());}
        void error(String code,String message,String target,Map<String,Object> details){errorCount++;if(errors.size()<MAX_DIAGNOSTICS)errors.add(diagnostic(code,message,target,details));}
        void warning(String code,String message,String target,Map<String,Object> details){warningCount++;if(warnings.size()<MAX_DIAGNOSTICS)warnings.add(diagnostic(code,message,target,details));}
        Map<String,Object> result(){return values("valid",errorCount==0,"mode","native-simulator-read-only-preflight","errors",errors,"warnings",warnings,"error_count",errorCount,"warning_count",warningCount,"diagnostics_truncated",errorCount>errors.size()||warningCount>warnings.size(),"counts",values("placement_records_total",total,"pending",pending,"placed",placed,"excluded",excluded,"excluded_reasons",excludedReasons),"material_requirements",materials,"tooling",tooling,"physical_qualification",false,"hardware_qualified",false,"side_effects_performed",false,"native_processor_preflight_executed",false);}
    }
    private static Map<String,Object> diagnostic(String code,String message,String target,Map<String,Object> details){return values("code",code,"message",message,"target_id",target,"details",details);}
    private static Map<String,Object> values(Object... pairs){Map<String,Object> result=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)result.put((String)pairs[i],pairs[i+1]);return result;}
}
