/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.Configuration;
import org.w3c.dom.*;

/** Rehashed hostile shapes are refused before native deserialization or destination reservation. */
public final class NativePortableAllocationTest {
    static int checks;interface Edit{void change(Document d)throws Exception;}
    static void check(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
    static void reject(Path root,Map<String,byte[]> original,String name,Edit edit)throws Exception {
        Map<String,byte[]> entries=new TreeMap<>(original);Document d=NativePortableConfiguration.xml(entries.get("config/machine.xml"));edit.change(d);entries.put("config/machine.xml",NativePortableConfiguration.encode(d));byte[] bytes=NativePortableConfigurationTest.rehash(entries);Path file=root.resolve(name+".zip"),destination=root.resolve(name);Files.write(file,bytes);
        try{NativePortableConfiguration.prepare(file,NativePortableConfiguration.sha(bytes),destination);throw new AssertionError("Accepted "+name);}catch(Bridge.Fault rejected){check("PORTABLE_PROFILE_LIMIT".equals(rejected.code),name+": "+rejected.code);}check(!Files.exists(destination),"rejected "+name+" before reservation/native read");
    }
    public static void main(String[] args)throws Exception {
        Path root=Files.createTempDirectory("openpnp-portable-allocation-");Configuration.initialize(root.resolve("source").toFile());Configuration c=Configuration.get();c.load();SimulatorMain.accelerateFixture(c);SimulatorMain.settleFreshFixture(root.resolve("source"));c=Configuration.get();
        try {
            Path archive=root.resolve("source.zip");NativePortableConfiguration.export(c,archive);Map<String,byte[]> original=NativePortableConfiguration.unzip(Files.readAllBytes(archive));
            reject(root,original,"camera-count",d->{Element camera=(Element)d.getElementsByTagName("camera").item(0);camera.getParentNode().appendChild(camera.cloneNode(true));});
            reject(root,original,"repeated-source",d->{Element camera=(Element)d.getElementsByTagName("camera").item(0),other=(Element)d.getElementsByTagName("camera").item(1);other.getParentNode().replaceChild(camera.cloneNode(true),other);});
            reject(root,original,"huge-frame",d->((Element)d.getElementsByTagName("camera").item(0)).setAttribute("width","2147483647"));
            reject(root,original,"huge-scale",d->((Element)d.getElementsByTagName("camera").item(0)).setAttribute("scale-height","2147483647"));
            reject(root,original,"huge-array",d->((Element)d.getElementsByTagName("camera-matrix").item(0)).setAttribute("length","2147483647"));
            reject(root,original,"stage-count",d->{Element stage=(Element)d.getElementsByTagName("cv-stage").item(0);for(int i=0;i<65;i++)stage.getParentNode().appendChild(stage.cloneNode(true));});
            reject(root,original,"capture-count",d->{Element stage=(Element)d.getElementsByTagName("cv-stage").item(0);stage.setAttribute("count","1000000");});
            check(!c.getMachine().isEnabled()&&!c.getMachine().isHomed(),"allocation refusals never enable/home");check(c.getMachine().getDefaultHead().getDefaultNozzle().getPart()==null,"no held material");
            System.out.println("OPENPNP_PORTABLE_ALLOCATION_RESULT "+NativePortableConfiguration.JSON.toJson(Bridge.map("checks",checks,"rejected_shapes",7,"native_feeds",0,"native_placements",0,"hardware_qualified",false)));
        }finally{c.getMachine().close();}
    }
}
