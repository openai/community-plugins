/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.Configuration;
import org.w3c.dom.*;

/** Regenerates admission digests from trusted native defaults; no imported XML is deserialized. */
public final class NativePortablePipelineTest {
    static int checks;
    static void check(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
    public static void main(String[] args)throws Exception {
        Path root=Files.createTempDirectory("openpnp-stock-pipeline-profile-");Configuration.initialize(root.resolve("config").toFile());Configuration c=Configuration.get();c.load();c.save();c.getMachine().close();Configuration.initialize(root.resolve("config").toFile());c=Configuration.get();c.load();c.save();
        Map<String,Set<String>> profiles=new TreeMap<>();List<Element> all=new ArrayList<>();
        try{
            for(String name:NativePortableConfiguration.CONFIG){Document d=NativePortableConfiguration.xml(Files.readAllBytes(root.resolve("config").resolve(name)));for(String tag:List.of("pipeline","cv-pipeline")){NodeList pipes=d.getElementsByTagName(tag);for(int i=0;i<pipes.getLength();i++){Element pipe=(Element)pipes.item(i);profiles.computeIfAbsent(NativePortablePipelines.context(pipe),k->new TreeSet<>()).add(NativePortablePipelines.fingerprint(pipe));all.add(pipe);}}}
            Files.writeString(root.resolve("stock-profiles.json"),NativePortableConfiguration.JSON.toJson(profiles));
            System.out.println("OPENPNP_STOCK_PIPELINE_PROFILES "+NativePortableConfiguration.JSON.toJson(profiles));
            if(args.length>0&&args[0].equals("--generate-only")){check(!c.getMachine().isEnabled()&&!c.getMachine().isHomed(),"trusted profile generation has no machine authority");return;}
            check(profiles.equals(NativePortablePipelines.TRUSTED),"pinned native stock pipeline profiles match compiled data");
            int variants=0;for(Element pipe:all){NativePortablePipelines.validate(pipe);NodeList stages=pipe.getElementsByTagName("cv-stage");for(int i=0;i<stages.getLength();i++){Element stage=(Element)stages.item(i);String old=stage.getAttribute("name");stage.setAttribute("name",old+"Unsupported");try{NativePortablePipelines.validate(pipe);throw new AssertionError("renamed stage admitted");}catch(Bridge.Fault e){check(e.code.equals("PORTABLE_PROFILE_LIMIT"),"unsupported name rejected");}finally{stage.setAttribute("name",old);}if(stage.getAttribute("class").endsWith(".Threshold")){String value=stage.getAttribute("threshold");stage.setAttribute("threshold","128");NativePortablePipelines.validate(pipe);NativePortableLimits.stage(stage);stage.setAttribute("threshold",value);variants++;}}}
            check(variants>0,"typed threshold edit accepted in stock-derived pipeline");check(!c.getMachine().isEnabled()&&!c.getMachine().isHomed(),"profile tests never enable or home");
            System.out.println("OPENPNP_PORTABLE_PIPELINE_RESULT "+NativePortableConfiguration.JSON.toJson(Bridge.map("checks",checks,"contexts",profiles.size(),"pipelines",all.size(),"native_feeds",0,"native_placements",0,"hardware_qualified",false)));
        }finally{c.getMachine().close();}
    }
}
