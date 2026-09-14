/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.io.RandomAccessFile;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.Configuration;

/** Sparse retained fixture exercises production aggregate admission without allocating 1 GiB. */
public final class NativeStorageBoundaryTest {
    public static void main(String[]args)throws Exception {
        Path root=Files.createTempDirectory("openpnp-native-storage-");Path config=root.resolve("config"),journal=root.resolve("journal"),token=root.resolve("token");Files.createDirectories(config);Files.createDirectories(journal);Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());long limit=1024L*1024*1024;
        Path existing=journal.resolve(UUID.randomUUID()+".artifact");try(RandomAccessFile sparse=new RandomAccessFile(existing.toFile(),"rw")){sparse.setLength(limit);}
        Configuration.initialize(config.toFile());Configuration.get().load();SimulatorMain.accelerateFixture(Configuration.get());Bridge bridge=new Bridge(Configuration.get(),token,journal,Paths.get(args[0]),0,true);int exit=0;
        try {
            Gson gson=new Gson();Map<?,?> caps=(Map<?,?>)bridge.call("openpnp_get_capabilities",new JsonObject());if(((Number)((Map<?,?>)caps.get("limits")).get("artifact_storage_bytes")).longValue()!=limit)throw new AssertionError("advertised storage limit");
            String session=(String)((Map<?,?>)bridge.call("openpnp_request_control_session",gson.toJsonTree(Bridge.map("request_id","storage-grant")).getAsJsonObject())).get("session_id");
            Map<?,?> op=(Map<?,?>)bridge.call("openpnp_capture_camera",gson.toJsonTree(Bridge.map("request_id","storage-capture","session_id",session,"mode","raw")).getAsJsonObject());String id=(String)op.get("operation_id");long deadline=System.nanoTime()+30_000_000_000L;
            do{op=(Map<?,?>)bridge.call("openpnp_get_operation",gson.toJsonTree(Bridge.map("operation_id",id)).getAsJsonObject());if("failed".equals(op.get("state")))break;Thread.sleep(10);}while(System.nanoTime()<deadline);
            if(!"failed".equals(op.get("state"))||!"ARTIFACT_STORAGE_CAPACITY".equals(((Map<?,?>)op.get("result")).get("code")))throw new AssertionError(op);
            Map<?,?> status=(Map<?,?>)bridge.call("openpnp_get_status",new JsonObject()),metrics=(Map<?,?>)status.get("metrics");if(((Number)metrics.get("artifact_bytes")).longValue()!=limit||((Number)metrics.get("artifact_count")).intValue()!=1||Files.size(existing)!=limit)throw new AssertionError("existing evidence changed");
            try(java.util.stream.Stream<Path> files=Files.list(journal)){if(files.filter(p->p.toString().endsWith(".artifact")).count()!=1)throw new AssertionError("rejected admission published artifact");}
            System.out.println("OPENPNP_NATIVE_STORAGE_RESULT "+gson.toJson(Bridge.map("passed",Arrays.asList("aggregate artifact content limit advertised and reconstructed from retained files","real camera capture rejected before publishing bytes at aggregate capacity","existing sparse evidence and metrics remain intact without deletion"),"sparse_fixture_bytes",limit,"upstream_commit",Bridge.UPSTREAM,"simulation_only",true,"physical_qualification",false)));
        }catch(Throwable e){e.printStackTrace();exit=1;}finally{bridge.close();Configuration.get().getMachine().close();Files.deleteIfExists(existing);}System.exit(exit);
    }
}
