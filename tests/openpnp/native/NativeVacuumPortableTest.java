/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.lang.management.ManagementFactory;
import java.lang.reflect.Field;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.ReferenceNozzleTip.VacuumMeasurementMethod;
import org.openpnp.model.Configuration;
import org.openpnp.spi.*;
import org.w3c.dom.*;

/** Disabled sensing portability and refusal before native loading or archive publication.
 * Uses real native save/load/adoption. Crafted archives are private negative fixtures. */
public final class NativeVacuumPortableTest {
    static final Gson G=new Gson();static int checks;
    interface Action {void run()throws Exception;}
    interface Mutation {void apply(Document d,Element tip)throws Exception;}
    static void check(boolean yes,String why){checks++;if(!yes)throw new AssertionError(why);}
    static void reject(Action action)throws Exception {try{action.run();throw new AssertionError("Expected sensing transfer refusal");}catch(Bridge.Fault e){check(NativePortableVacuum.CODE.equals(e.code),"Expected sensing transfer refusal, got "+e.code);}}
    static void field(Document d,Element tip,String key,String value){NodeList list=tip.getElementsByTagName(key);Element e=list.getLength()==0?d.createElement(key):(Element)list.item(0);if(e.getParentNode()==null)tip.appendChild(e);e.setTextContent(value);}
    static void remove(Element tip,String key){NodeList list=tip.getElementsByTagName(key);if(list.getLength()!=0)tip.removeChild(list.item(0));}
    public static void main(String[] args)throws Exception {
        if(args.length==2){phase(args[0],Path.of(args[1]));return;}
        Path root=Files.createTempDirectory("vacuum-portable78-");
        for(String phase:List.of("seed","produce","adopt-stock","adopt-disabled","verify-and-activation-refusal")) {
            List<String> cmd=new ArrayList<>();cmd.add(Path.of(System.getProperty("java.home"),"bin/java").toString());cmd.addAll(ManagementFactory.getRuntimeMXBean().getInputArguments());cmd.addAll(List.of("-cp",System.getProperty("java.class.path"),NativeVacuumPortableTest.class.getName(),phase,root.toString()));
            Process child=new ProcessBuilder(cmd).redirectErrorStream(true).redirectOutput(root.resolve(phase+".log").toFile()).start();boolean exit=child.waitFor(90,TimeUnit.SECONDS);if(!exit){child.destroyForcibly();child.waitFor(5,TimeUnit.SECONDS);}check(exit&&child.exitValue()==0,"Owned phase "+phase+" exited="+exit+" code="+(child.isAlive()?"alive":child.exitValue())+": "+Files.readString(root.resolve(phase+".log")));
        }
        System.out.println("OPENPNP_NATIVE_VACUUM_PORTABLE_RESULT "+G.toJson(Bridge.map("checks",checks,"fresh_process_phases",5,"source_authority_transferred",false,"native_sensor_reads",0,"physical_qualification",false,"root",root.toString())));System.exit(0);
    }
    static void phase(String phase,Path root)throws Exception {
        try {
            if(phase.equals("seed")){Configuration.initialize(root.resolve("config").toFile());Configuration c=Configuration.get();c.load();try{SimulatorMain.accelerateFixture(c);c.save();}finally{c.getMachine().close();}}
            else if(phase.equals("produce"))produce(root);
            else if(phase.startsWith("adopt-")){String kind=phase.substring(6);Path zip=root.resolve(kind+".zip");NativePortableConfiguration.Prepared p=NativePortableConfiguration.prepare(zip,NativePortableConfiguration.sha(Files.readAllBytes(zip)),root.resolve(kind+"-adopted"));NativePortableConfiguration.validateAndPublish(p);check(Files.isRegularFile(p.destination.resolve("adoption.json")),"Disabled source produced a native roundtrip activation");}
            else verify(root);
            System.out.println("OPENPNP_NATIVE_VACUUM_PORTABLE_PHASE "+G.toJson(Bridge.map("phase",phase,"checks",checks,"source_authority_transferred",false)));System.exit(0);
        }catch(Throwable e){e.printStackTrace();System.exit(1);}
    }
    static void produce(Path root)throws Exception {
        Configuration.initialize(root.resolve("config").toFile());Configuration c=Configuration.get();c.load();Machine m=c.getMachine();
        try {
            SimulatorMain.accelerateFixture(c);ReferenceNozzle n=(ReferenceNozzle)m.getDefaultHead().getDefaultNozzle();ReferenceNozzleTip tip=n.getNozzleTip();
            check(tip.getMethodPartOn()==VacuumMeasurementMethod.None&&tip.getMethodPartOff()==VacuumMeasurementMethod.None&&tip.isPartOnCheckAfterPick()&&tip.isPartOffCheckAfterPlace(),"Real stock None methods retain inactive true check flags");
            NativePortableConfiguration.export(c,root.resolve("stock.zip"));String saved=NativePortableConfiguration.sha(Files.readAllBytes(root.resolve("config/machine.xml")));
            ReferenceNozzleTip unused=new ReferenceNozzleTip();unused.setMethodPartOn(VacuumMeasurementMethod.None);unused.setMethodPartOff(VacuumMeasurementMethod.None);m.addNozzleTip(unused);
            unused.setMethodPartOn(VacuumMeasurementMethod.Absolute);unused.setPartOnCheckAfterPick(false);unused.setPartOnCheckAlign(false);unused.setPartOnCheckBeforePlace(false);
            reject(()->NativePortableConfiguration.export(c,root.resolve("unused-enabled.zip")));check(!Files.exists(root.resolve("unused-enabled.zip"))&&saved.equals(NativePortableConfiguration.sha(Files.readAllBytes(root.resolve("config/machine.xml")))),"Unused enabled tip refused before config save/archive write, even with disabled flags");
            unused.setMethodPartOn(VacuumMeasurementMethod.Difference);reject(()->NativePortableConfiguration.quiescent(c));unused.setMethodPartOn(VacuumMeasurementMethod.None);
            unused.setMethodPartOff(VacuumMeasurementMethod.Absolute);reject(()->NativePortableConfiguration.quiescent(c));unused.setMethodPartOff(VacuumMeasurementMethod.None);
            for(boolean on:List.of(true,false)){if(on)unused.setEstablishPartOnLevel(true);else unused.setEstablishPartOffLevel(true);reject(()->NativePortableConfiguration.quiescent(c));unused.setEstablishPartOnLevel(false);unused.setEstablishPartOffLevel(false);}
            unused.setMethodPartOn(null);reject(()->NativePortableConfiguration.quiescent(c));Field raw=ReferenceNozzleTip.class.getDeclaredField("methodPartOn");raw.setAccessible(true);check(raw.get(unused)==null,"Read-only transfer check does not normalize unknown method to None");unused.setMethodPartOn(VacuumMeasurementMethod.None);m.removeNozzleTip(unused);
            ReferenceNozzleTip detached=new ReferenceNozzleTip();detached.setMethodPartOn(VacuumMeasurementMethod.Absolute);detached.setMethodPartOff(VacuumMeasurementMethod.None);c.getPackages().get(0).addCompatibleNozzleTip(detached);reject(()->NativePortableConfiguration.quiescent(c));c.getPackages().get(0).removeCompatibleNozzleTip(detached);
            ReferenceNozzle shared=new ReferenceNozzle();shared.addCompatibleNozzleTip(tip);n.getHead().addNozzle(shared);tip.setMethodPartOn(VacuumMeasurementMethod.Absolute);reject(()->NativePortableConfiguration.quiescent(c));tip.setMethodPartOn(VacuumMeasurementMethod.None);n.getHead().removeNozzle(shared);
            n.setVacuumSenseActuator(null);for(NozzleTip t:m.getNozzleTips()){ReferenceNozzleTip r=(ReferenceNozzleTip)t;r.setPartOnCheckAfterPick(false);r.setPartOnCheckAlign(false);r.setPartOnCheckBeforePlace(false);r.setPartOffCheckAfterPlace(false);r.setPartOffCheckBeforePick(false);}
            NativePortableConfiguration.export(c,root.resolve("disabled.zip"));check(!m.isEnabled()&&!m.isHomed()&&n.getPart()==null,"Portable settings exports never grant motion/held state");
            Map<String,byte[]> original=NativePortableConfiguration.unzip(Files.readAllBytes(root.resolve("stock.zip")));
            for(String method:List.of("Absolute","Difference","unknown","none"))negative(root,original,"method-"+method,(d,t)->field(d,t,"method-part-on",method));
            negative(root,original,"off-absolute",(d,t)->field(d,t,"method-part-off","Absolute"));
            negative(root,original,"legacy-on",(d,t)->{remove(t,"method-part-on");field(d,t,"vacuum-level-part-on-low","0");field(d,t,"vacuum-level-part-on-high","1");});
            negative(root,original,"legacy-off",(d,t)->{remove(t,"method-part-off");field(d,t,"vacuum-level-part-off-low","0");field(d,t,"vacuum-level-part-off-high","1");});
            negative(root,original,"legacy-nonfinite",(d,t)->{remove(t,"method-part-on");field(d,t,"vacuum-level-part-on-low","NaN");});
            for(String flag:List.of("establish-part-on-level","establish-part-off-level"))for(String value:List.of("true","unknown"))negative(root,original,flag+value,(d,t)->t.setAttribute(flag,value));
            negative(root,original,"duplicate-method",(d,t)->{Element duplicate=d.createElement("method-part-on");duplicate.setTextContent("None");t.appendChild(duplicate);});
            Document none=NativePortableConfiguration.xml(original.get("config/machine.xml"));Element t=(Element)none.getElementsByTagName("nozzle-tip").item(0);remove(t,"method-part-on");field(none,t,"vacuum-level-part-on-low","0");field(none,t,"vacuum-level-part-on-high","0");NativePortableConfiguration.validate(none,"machine.xml");check(true,"Disabled legacy zero thresholds pass pre-load XML semantics");
        }finally{m.close();}
    }
    static void negative(Path root,Map<String,byte[]> original,String id,Mutation mutation)throws Exception {
        Map<String,byte[]> entries=new TreeMap<>(original);Document d=NativePortableConfiguration.xml(entries.get("config/machine.xml"));mutation.apply(d,(Element)d.getElementsByTagName("nozzle-tip").item(0));entries.put("config/machine.xml",NativePortableConfiguration.encode(d));byte[] bytes=NativePortableConfigurationTest.rehash(entries);Path archive=root.resolve(id+".zip"),destination=root.resolve(id+"-destination");Files.write(archive,bytes);
        reject(()->NativePortableConfiguration.prepare(archive,NativePortableConfiguration.sha(bytes),destination));check(!Files.exists(destination),"Rehashed unsafe "+id+" archive refused before reservation/staging/native load");
    }
    static void verify(Path root)throws Exception {
        Path destination=root.resolve("disabled-adopted");Path active=NativePortableConfiguration.activeConfiguration(destination);Configuration.initialize(active.toFile());Configuration c=Configuration.get();c.load();Machine m=c.getMachine();
        try {
            ReferenceNozzle n=(ReferenceNozzle)m.getDefaultHead().getDefaultNozzle();ReferenceNozzleTip tip=n.getNozzleTip();
            check(n.getVacuumSenseActuator()==null&&tip.getMethodPartOn()==VacuumMeasurementMethod.None&&tip.getMethodPartOff()==VacuumMeasurementMethod.None&&!tip.isPartOnCheckAfterPick()&&!tip.isPartOffCheckAfterPlace(),"Fresh actual adoption preserves disabled None/null binding and flags");
            check("native-driver".equals(VacuumSensing.sourceProvenance(n).get("origin")),"Portable native load carries no controlled source authority");NativePortableConfiguration.quiescent(c);
            tip.setMethodPartOff(VacuumMeasurementMethod.Absolute);reject(()->NativePortableConfiguration.quiescent(c));tip.setMethodPartOff(VacuumMeasurementMethod.None);
            check(!m.isEnabled()&&!m.isHomed()&&n.getPart()==null,"Adoption and post-load sensing guard performed no native actions");
        }finally{m.close();}
        // Simulate a coherently rehashed prior-version local generation, not only tampering.
        // Existing integrity checks pass; the current activation schema must still reject its
        // enabled sensing before any Configuration.initialize/load or source installation.
        Path stage=active.getParent(),machine=active.resolve("machine.xml");Document d=NativePortableConfiguration.xml(Files.readAllBytes(machine));field(d,(Element)d.getElementsByTagName("nozzle-tip").item(0),"method-part-off","Absolute");Files.write(machine,NativePortableConfiguration.encode(d));
        Path receipt=destination.resolve("adoption.json");JsonObject r=new JsonParser().parse(Files.readString(receipt)).getAsJsonObject();r.add("generation_files_sha256",G.toJsonTree(NativePortableConfiguration.activationFiles(stage)));Files.writeString(receipt,G.toJson(r));
        check(Files.isSameFile(receipt,stage.resolve("activation.receipt.json")),"Negative activation fixture retains exact native published hardlink structure");reject(()->NativePortableConfiguration.activeConfiguration(destination));
    }
}
