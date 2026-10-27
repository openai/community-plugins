/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.util.*;
import org.openpnp.machine.reference.ReferenceNozzleTip;
import org.openpnp.machine.reference.ReferenceNozzleTip.VacuumMeasurementMethod;
import org.openpnp.model.Configuration;
import org.openpnp.spi.*;
import org.w3c.dom.*;

/** Read-only transfer refusal, not sensing admission. Live source callbacks and occupancy
 * authority are never serialized. Check flags are inactive when both native methods are None. */
final class NativePortableVacuum {
    private NativePortableVacuum() { }
    static final String CODE="SENSING_TRANSFER_UNSUPPORTED";

    static void requireDisabled(Configuration config)throws Exception {
        Machine machine=config.getMachine();
        Set<NozzleTip> tips=Collections.newSetFromMap(new IdentityHashMap<>());
        if(machine.getNozzleTips().size()>128||machine.getHeads().size()>8||config.getPackages().size()>512)reject("Sensing transfer graph exceeds bounded inventory");
        tips.addAll(machine.getNozzleTips());int nozzles=0,references=0;
        for(Head head:machine.getHeads())for(Nozzle nozzle:head.getNozzles()) {
            if(++nozzles>32)reject("Sensing transfer graph exceeds bounded nozzle inventory");
            if(nozzle.getNozzleTip()!=null)tips.add(nozzle.getNozzleTip());
            Set<NozzleTip> compatible=nozzle.getCompatibleNozzleTips();references+=compatible.size();
            if(references>65536)reject("Sensing transfer graph exceeds bounded reference inventory");tips.addAll(compatible);
        }
        for(org.openpnp.model.Package pkg:config.getPackages()) {
            Set<NozzleTip> compatible=pkg.getCompatibleNozzleTips();references+=compatible.size();
            if(references>65536)reject("Sensing transfer graph exceeds bounded reference inventory");tips.addAll(compatible);
        }
        if(tips.size()>128)reject("Sensing transfer graph exceeds bounded tip inventory");
        for(NozzleTip raw:tips) {
            if(raw==null||raw.getClass()!=ReferenceNozzleTip.class)reject("Unknown native nozzle tip cannot transfer sensing authority");
            ReferenceNozzleTip tip=(ReferenceNozzleTip)raw;
            // The public getters assign None when the underlying field is null. Reading only
            // these two fixed fields of the exact pinned class preserves unknown state and
            // prevents inspection from silently normalizing a legacy/invalid configuration.
            if(method(tip,true)!=VacuumMeasurementMethod.None||method(tip,false)!=VacuumMeasurementMethod.None
                ||tip.isEstablishPartOnLevel()||tip.isEstablishPartOffLevel())
                reject("Every canonical, installed and compatible tip must disable sensing before portable export/adoption: "+tip.getId());
        }
    }
    private static VacuumMeasurementMethod method(ReferenceNozzleTip tip,boolean partOn)throws Exception {
        VacuumMeasurementMethod value=NativeVacuumSettings.readMethod(tip,partOn);
        if(value==null)reject("Unknown native sensing method cannot transfer");return value;
    }

    /** Before native load: absent legacy methods can become Absolute in configurationComplete.
     * Apply that exact low<high migration rule without loading or mutating any native model. */
    static void requireDisabled(Document document)throws Exception {
        NodeList all=document.getElementsByTagName("*");
        for(int i=0;i<all.getLength();i++) {
            Element tip=(Element)all.item(i);
            if(!"nozzle-tip".equals(tip.getTagName())&&!ReferenceNozzleTip.class.getName().equals(tip.getAttribute("class")))continue;
            for(String phase:List.of("on","off")) {
                String establish="establish-part-"+phase+"-level";
                if(tip.hasAttribute(establish)&&!"false".equals(tip.getAttribute(establish)))reject("Sensing establishment or an unknown establishment flag cannot transfer");
                String method=element(tip,"method-part-"+phase);
                if(method!=null) {
                    if(!"None".equals(method))reject("Enabled or unknown native sensing method cannot transfer");
                }else {
                    double low=legacyNumber(tip,"vacuum-level-part-"+phase+"-low"),high=legacyNumber(tip,"vacuum-level-part-"+phase+"-high");
                    if(low<high)reject("Legacy vacuum thresholds would enable Absolute sensing during native load");
                }
            }
        }
    }
    private static String element(Element parent,String name)throws Exception {
        String value=null;boolean found=false;
        for(Node n=parent.getFirstChild();n!=null;n=n.getNextSibling())if(n instanceof Element&&name.equals(((Element)n).getTagName())) {
            if(found)reject("Duplicated native sensing field cannot transfer");found=true;
            Element e=(Element)n;if(e.getElementsByTagName("*").getLength()!=0||e.getTextContent().length()>128)reject("Malformed native sensing field cannot transfer");value=e.getTextContent();
        }
        return value;
    }
    private static double legacyNumber(Element tip,String name)throws Exception {
        String text=element(tip,name);if(text==null)return 0;
        try {double value=Double.parseDouble(text);if(!Double.isFinite(value))reject("Nonfinite legacy sensing threshold cannot transfer");return value;}
        catch(NumberFormatException failure){reject("Invalid legacy sensing threshold cannot transfer");return 0;}
    }
    private static void reject(String message)throws Bridge.Fault {throw new Bridge.Fault(CODE,message);}
}
