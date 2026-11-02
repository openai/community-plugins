/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.security.MessageDigest;
import org.openpnp.model.Configuration;
import org.openpnp.spi.Machine;

/** Fresh-JVM replay of copied closed actual Bridge records. Mutants are explicitly adversarial history. */
public final class NativeVacuumBridgeRecoveryTest {
    static final Gson JSON=new GsonBuilder().serializeNulls().create();static int checks;
    static void check(boolean ok,String reason){checks++;if(!ok)throw new AssertionError(reason);}
    static String hash(Path p)throws Exception{StringBuilder out=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(p)))out.append(String.format("%02x",b));return out.toString();}
    @SuppressWarnings("unchecked")public static void main(String[]args)throws Exception {
        Path samples=Path.of(args[0]),source=Path.of(args[1]),out=Path.of(args[2]);String mode=args.length>3?args[3]:"control";Files.createDirectories(out);Path journal=out.resolve("journal");Files.createDirectories(journal);
        String original=hash(source.resolve("operations.jsonl"));Files.copy(source.resolve("machine-id"),journal.resolve("machine-id"));Files.copy(source.resolve("operations.jsonl"),journal.resolve("operations.jsonl"));
        if(!mode.equals("control")){
            List<String> lines=Files.readAllLines(journal.resolve("operations.jsonl"));boolean edited=false;
            for(int i=0;i<lines.size();i++){
                JsonObject row=new JsonParser().parse(lines.get(i)).getAsJsonObject();if(mode.startsWith("lifecycle-")){
                    if(!row.get("type").getAsString().equals("vacuum_lifecycle_uncertain"))continue;
                    JsonObject payload=row.getAsJsonObject("payload");
                    if(mode.equals("lifecycle-foreign-request"))payload.addProperty("request_id",UUID.randomUUID().toString());
                    else if(mode.equals("lifecycle-foreign-config"))payload.addProperty("config_revision","cfg-999");
                    else throw new IllegalArgumentException("Unknown lifecycle mutant");
                    lines.set(i,JSON.toJson(row));edited=true;break;
                }
                if(!row.get("type").getAsString().startsWith("vacuum_observation"))continue;
                JsonObject p=row.getAsJsonObject("payload"),c=p.getAsJsonObject("context");
                if(mode.equals("foreign-request"))c.addProperty("request_id",UUID.randomUUID().toString());
                else if(mode.equals("foreign-operation"))c.addProperty("operation_id",UUID.randomUUID().toString());
                else if(mode.equals("foreign-config"))c.addProperty("config_revision","cfg-999");
                else if(mode.equals("foreign-load")){if(c.get("job_context").isJsonNull())continue;c.getAsJsonObject("job_context").addProperty("board_load_revision","load-999");}
                else if(mode.equals("duplicate-key")){lines.set(i,lines.get(i).replace("\"native_event\":","\"native_event\":\"read.before\",\"native_event\":"));edited=true;break;}
                else throw new IllegalArgumentException("Unknown mutant");lines.set(i,JSON.toJson(row));edited=true;break;
            }
            check(edited,"Adversarial mutation actually targeted a native sensing record");Files.writeString(journal.resolve("operations.jsonl"),String.join("\n",lines)+"\n");
        }
        Path configDir=out.resolve("config");Configuration.initialize(configDir.toFile());Configuration config=Configuration.get();config.load();Machine machine=config.getMachine();Files.writeString(out.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID());Bridge bridge=null;Throwable failure=null;
        try {
            try{bridge=new Bridge(config,out.resolve("token"),journal,samples,0,true);if(!mode.equals("control"))throw new AssertionError("Contradictory history admitted");}
            catch(java.io.IOException rejected){if(mode.equals("control"))throw rejected;check(rejected.getMessage().contains("malformed"),"Contradictory exact history rejected");}
            if(bridge!=null){
                Map<String,Object> status=(Map<String,Object>)bridge.call("openpnp_get_status",new JsonObject());Map<?,?> sensing=(Map<?,?>)status.get("vacuum_sensing_journal");
                check(Boolean.TRUE.equals(sensing.get("recovered_history")),"Native observations remain historical");check(Boolean.FALSE.equals(sensing.get("execution_authority_restored")),"Replay restores no execution authority");
                for(Object value:(List<?>)sensing.get("nozzles"))check(Boolean.TRUE.equals(((Map<?,?>)value).get("historical")),"Every recorded nozzle is historical");
                check(!machine.isEnabled()&&!machine.isHomed(),"Recovery starts disabled and unhomed");
                boolean fault=Boolean.TRUE.equals(sensing.get("lifecycle_fault"))||((List<?>)sensing.get("nozzles")).stream().anyMatch(n->Boolean.TRUE.equals(((Map<?,?>)n).get("sticky_fault")));
                String session=(String)((Map<?,?>)bridge.call("openpnp_request_control_session",JSON.toJsonTree(Bridge.map("request_id",UUID.randomUUID().toString(),"ttl_seconds",300)).getAsJsonObject())).get("session_id");
                JsonObject request=JSON.toJsonTree(Bridge.map("session_id",session,"request_id",UUID.randomUUID().toString(),"expected_config_revision",status.get("config_revision"),"enabled",true)).getAsJsonObject();
                if(fault)try{bridge.call("openpnp_set_machine_enabled",request);throw new AssertionError("Recovered retained/unknown evidence was cleared");}catch(Bridge.Fault refused){check("VACUUM_OUTCOME_UNKNOWN".equals(refused.code),"Sticky sensing evidence survives new lease/source/config");}
            }
        }catch(Throwable e){failure=e;e.printStackTrace();}
        finally{if(bridge!=null)bridge.close();machine.close();check(original.equals(hash(source.resolve("operations.jsonl"))),"Original closed producer journal unchanged");Map<String,Object> receipt=Bridge.map("passed",failure==null,"mode",mode,"checks",checks,"source_journal_sha256",original,"copied_journal_sha256",hash(journal.resolve("operations.jsonl")),"failure",failure==null?null:failure.toString(),"adversarial_history",!mode.equals("control"),"native_sensor_calls",0,"native_job_calls",0,"execution_authority_restored",false);Files.writeString(out.resolve("result.json"),JSON.toJson(receipt));System.out.println("VACUUM_BRIDGE_RECOVERY "+JSON.toJson(receipt));}
        System.exit(failure==null?0:1);
    }
}
