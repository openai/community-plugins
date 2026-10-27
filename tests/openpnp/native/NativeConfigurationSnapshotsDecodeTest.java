/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.function.Consumer;
import org.openpnp.model.Configuration;
import org.openpnp.machine.reference.feeder.ReferenceStripFeeder;
import org.openpnp.spi.Machine;

/** Passive real-model restore tests. No controller enable, homing, pick, feed or motion calls. */
public final class NativeConfigurationSnapshotsDecodeTest {
    static final Gson GSON=new Gson();
    static final List<String> passed=new ArrayList<>();
    static Configuration config;static Machine machine;static ReferenceStripFeeder feeder;
    static JsonObject baseline;
    @FunctionalInterface interface Action {void run()throws Exception;}
    static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    static void reject(String code,Action action)throws Exception{try{action.run();throw new AssertionError("Expected "+code);}catch(Bridge.Fault failure){check(code.equals(failure.code),"Expected "+code+", got "+failure.code+": "+failure.getMessage());}}
    static JsonObject object(Object...pairs){return GSON.toJsonTree(Bridge.map(pairs)).getAsJsonObject();}
    static JsonObject copy(JsonObject source){return new JsonParser().parse(source.toString()).getAsJsonObject();}
    static JsonObject document(NativeConfigurationSnapshots.Snapshot snapshot){return GSON.toJsonTree(snapshot.document).getAsJsonObject();}
    static JsonObject row(JsonObject document,String type){for(JsonElement value:document.getAsJsonArray("typed_changes")){JsonObject row=value.getAsJsonObject();if(type.equals(row.get("type").getAsString()))return row;}throw new AssertionError("Missing captured type: "+type);}
    static void bad(Consumer<JsonObject> edit)throws Exception{JsonObject value=copy(baseline);edit.accept(value);reject("SNAPSHOT_INVALID",()->NativeConfigurationSnapshots.decode(value));}
    static void bind()throws Exception{machine=config.getMachine();feeder=(ReferenceStripFeeder)machine.getFeeders().stream().filter(value->value.getClass()==ReferenceStripFeeder.class).findFirst().orElseThrow();}
    static void onlyChanges(JsonObject value,JsonObject...rows){JsonArray changes=new JsonArray();for(JsonObject row:rows)changes.add(row);value.add("typed_changes",changes);}
    static JsonObject speed(double value){return object("type","set_machine_speed","speed",value);}
    static JsonObject enabled(boolean value){return object("type","set_feeder_enabled","feeder_id",feeder.getId(),"enabled",value);}

    public static void main(String[] args)throws Exception {
        Path directory=Files.createTempDirectory("openpnp-snapshot-decode-");Configuration.initialize(directory.toFile());config=Configuration.get();config.load();bind();int exit=0;
        try {
            check(!machine.isEnabled()&&!machine.isHomed(),"native fixture starts disabled/unhomed");
            machine.setSpeed(0.5);feeder.setMaxFeedCount(40);feeder.setFeedCount(2);feeder.setEnabled(true);
            baseline=document(NativeConfigurationSnapshots.capture(config));NativeConfigurationSnapshots.decode(copy(baseline));
            check(row(baseline,"set_strip_feeder_geometry").get("feed_count").getAsInt()==2,"actual strip count captured");
            passed.add("a current real-native capture decodes with pinned version, scope, identities and explicit material count");
            envelopeRejections();inventoryAndCounts();exactNativeStaging();structuralBounds();legacyAndCopies();materialAndFaultHistory();
            // A fresh Configuration instance loads newer native material state from actual XML.
            // The typed document itself is JSON; the helper never reads that native XML.
            Path retained=directory.resolve("test-owned-typed-snapshot.json");Files.write(retained,baseline.toString().getBytes(StandardCharsets.UTF_8));
            machine.setSpeed(0.3);feeder.setFeedCount(7);feeder.setEnabled(false);String id=feeder.getId();config.save();machine.close();
            Configuration.initialize(directory.toFile());config=Configuration.get();config.load();bind();
            check(feeder.getId().equals(id)&&feeder.getFeedCount()==7&&!feeder.isEnabled(),"native configuration reload retains newer material state");
            JsonObject persisted=new JsonParser().parse(Files.readString(retained,StandardCharsets.UTF_8)).getAsJsonObject();
            NativeConfigurationSnapshots.Restore restore=NativeConfigurationSnapshots.prepareRestore(config,NativeConfigurationSnapshots.decode(persisted));
            check(machine.getSpeed()==0.3,"decoded restore stages passively after fresh configuration load");restore.apply();
            check(machine.getSpeed()==0.5&&feeder.getFeedCount()==7&&!feeder.isEnabled(),"restart-decoded rollback restores representable settings and preserves newer consumption/disable");
            check(!machine.isEnabled()&&!machine.isHomed(),"snapshot decode/rollback does not enable or home the machine");
            passed.add("persisted JSON decodes after fresh native Configuration.load and retains newer native material counts and disable state");
            System.out.println("OPENPNP_NATIVE_SNAPSHOT_DECODE_RESULT "+GSON.toJson(Bridge.map("passed",passed,"upstream_commit",Bridge.UPSTREAM,"simulation_only",true,"physical_qualification",false,"motion_performed",false,"feed_operations_performed",0,"artifact_authority_verified_by_this_helper",false)));
        }catch(Throwable failure){failure.printStackTrace();exit=1;}finally{machine.close();}System.exit(exit);
    }

    static void envelopeRejections()throws Exception {
        bad(d->d.addProperty("version",2));bad(d->d.addProperty("version","1"));bad(d->d.addProperty("version",1.1));
        bad(d->d.addProperty("upstream_commit","other-upstream"));bad(d->d.addProperty("scope","full-configuration"));
        for(String claim:Arrays.asList("physical_state_restored","full_configuration_restore","job_placed_history_replaced"))bad(d->d.addProperty(claim,true));
        bad(d->d.addProperty("newer_material_counts_preserved",false));bad(d->d.addProperty("physical_state_restored","false"));
        bad(d->d.addProperty("arbitrary_xml_path","/arbitrary/machine.xml"));bad(d->d.remove("settings_readback"));
        bad(d->row(d,"set_machine_speed").addProperty("type","create_part"));bad(d->row(d,"set_machine_speed").addProperty("type","set_arbitrary_property"));
        bad(d->{JsonArray omissions=new JsonArray();omissions.add(object("type","x","target_id","x"));d.add("omissions",omissions);});
        passed.add("unsupported versions/upstreams/scopes/claims, missing envelope fields, unknown/create types and malformed omissions reject");
    }

    static void inventoryAndCounts()throws Exception {
        bad(d->d.getAsJsonObject("identity_inventory").remove("feeders"));
        bad(d->d.getAsJsonObject("identity_inventory").add("actuators",new JsonArray()));
        bad(d->{JsonArray list=d.getAsJsonObject("identity_inventory").getAsJsonArray("parts");list.add(new JsonPrimitive(list.get(0).getAsString().toLowerCase()));});
        bad(d->d.getAsJsonObject("identity_inventory").getAsJsonArray("feeders").add(new JsonPrimitive("")));
        bad(d->row(d,"set_feeder_enabled").addProperty("feeder_id","outside-inventory"));
        bad(d->d.getAsJsonObject("feed_counts_at_capture").addProperty("outside-inventory",3));
        for(JsonPrimitive invalid:Arrays.asList(new JsonPrimitive(-1),new JsonPrimitive(1.5),new JsonPrimitive("2"),new JsonPrimitive(2147483648L)))bad(d->d.getAsJsonObject("feed_counts_at_capture").add(feeder.getId(),invalid));
        bad(d->d.getAsJsonObject("feed_counts_at_capture").add(feeder.getId(),new JsonParser().parse("2.00000000000000001")));
        bad(d->d.getAsJsonObject("feed_counts_at_capture").addProperty(feeder.getId(),3));
        bad(d->row(d,"set_strip_feeder_geometry").addProperty("feed_count",2.5));
        // Omitted geometry does not permit omitting the consumption baseline for enabled/assign edits.
        JsonObject absent=copy(baseline);onlyChanges(absent,enabled(true));absent.getAsJsonObject("feed_counts_at_capture").remove(feeder.getId());
        NativeConfigurationSnapshots.Snapshot decoded=NativeConfigurationSnapshots.decode(absent);
        reject("SNAPSHOT_INVALID",()->NativeConfigurationSnapshots.prepareRestore(config,decoded));
        check(feeder.getFeedCount()==2&&machine.getSpeed()==0.5,"invalid count evidence changes no model");
        passed.add("identity inventories and counters reject unknown/duplicate identities, nonintegral/negative/overflow counts and missing consumption evidence");
    }

    static void exactNativeStaging()throws Exception {
        // The redundant enabled row would be filtered later. It still must pass the exact schema.
        JsonObject value=copy(baseline),badEnabled=enabled(true);badEnabled.addProperty("discard_fault_history",true);
        onlyChanges(value,speed(0.4),badEnabled);NativeConfigurationSnapshots.Snapshot snapshot=NativeConfigurationSnapshots.decode(value);
        reject("UNKNOWN_FIELD",()->NativeConfigurationSnapshots.prepareRestore(config,snapshot));
        check(machine.getSpeed()==0.5&&feeder.isEnabled(),"filtered-row schema error rejects before any preceding change");
        JsonObject late=copy(baseline);JsonArray changes=new JsonArray();for(int i=0;i<101;i++)changes.add(speed(0.4));changes.add(speed(0));late.add("typed_changes",changes);
        NativeConfigurationSnapshots.Snapshot many=NativeConfigurationSnapshots.decode(late);reject("OUT_OF_RANGE",()->NativeConfigurationSnapshots.prepareRestore(config,many));
        check(machine.getSpeed()==0.5,"late invalid row in a second native chunk cannot partially apply first chunk");
        JsonObject wrong=copy(baseline);JsonObject invalid=enabled(true);invalid.addProperty("enabled","true");onlyChanges(wrong,invalid);
        NativeConfigurationSnapshots.Snapshot wrongType=NativeConfigurationSnapshots.decode(wrong);reject("INVALID_ARGUMENT",()->NativeConfigurationSnapshots.prepareRestore(config,wrongType));
        passed.add("every original row stages against exact native schemas before filtering/count adjustment, including late errors beyond one hundred changes");
    }

    static void structuralBounds()throws Exception {
        bad(d->{JsonArray rows=new JsonArray();for(int i=0;i<10001;i++)rows.add(speed(0.5));d.add("typed_changes",rows);});
        bad(d->{JsonArray ids=new JsonArray();for(int i=0;i<10001;i++)ids.add(new JsonPrimitive("part-"+i));d.getAsJsonObject("identity_inventory").add("parts",ids);});
        bad(d->{JsonObject current=d.getAsJsonObject("settings_readback");for(int i=0;i<25;i++){JsonObject child=new JsonObject();current.add("child",child);current=child;}});
        bad(d->d.getAsJsonObject("settings_readback").addProperty("nonfinite",Double.POSITIVE_INFINITY));
        bad(d->{String large="x".repeat(1024*1024);for(int i=0;i<8;i++)d.getAsJsonObject("settings_readback").addProperty("bounded-string-"+i,large);});
        passed.add("ten-thousand-change/identity, nesting, nonfinite numeric and eight-MiB document limits reject before native staging");
    }

    static void legacyAndCopies()throws Exception {
        JsonObject legacy=copy(baseline);legacy.getAsJsonObject("identity_inventory").remove("heads");JsonArray older=new JsonArray();
        for(JsonElement item:legacy.getAsJsonArray("typed_changes"))if(!item.getAsJsonObject().has("head_id"))older.add(item);legacy.add("typed_changes",older);
        NativeConfigurationSnapshots.decode(legacy);
        bad(d->d.getAsJsonObject("identity_inventory").remove("heads"));
        JsonObject input=copy(baseline);onlyChanges(input,speed(0.5));NativeConfigurationSnapshots.Snapshot snapshot=NativeConfigurationSnapshots.decode(input);
        row(input,"set_machine_speed").addProperty("speed",0.2);((JsonArray)snapshot.document.get("typed_changes")).get(0).getAsJsonObject().addProperty("speed",0.1);
        ((Map<?,?>)snapshot.document.get("identity_inventory")).clear();((Map<?,?>)snapshot.document.get("feed_counts_at_capture")).clear();
        machine.setSpeed(0.3);NativeConfigurationSnapshots.prepareRestore(config,snapshot).apply();check(machine.getSpeed()==0.5,"caller/public metadata mutations cannot change privately staged snapshot settings or identity binding");
        passed.add("older version-one snapshots may omit unused heads inventory; private restore state is independent of caller/document mutation");
    }

    static void materialAndFaultHistory()throws Exception {
        NativeConfigurationSnapshots.Snapshot snapshot=NativeConfigurationSnapshots.decode(copy(baseline));
        feeder.recordJobFault(100,6,new Exception("Decode-test native fault history; no feed or machine operation"));String fault=feeder.summariseJobFaults();
        check(!fault.isEmpty(),"actual native feeder fault history populated");
        NativeConfigurationSnapshots.prepareRestore(config,snapshot).apply();check(feeder.isEnabled()&&fault.equals(feeder.summariseJobFaults()),"redundant true flag must not invoke native fault-clearing setter");
        feeder.setEnabled(false);NativeConfigurationSnapshots.Restore disabled=NativeConfigurationSnapshots.prepareRestore(config,snapshot);disabled.apply();
        check(!feeder.isEnabled()&&fault.equals(feeder.summariseJobFaults()),"fault-disabled feeder and observations survive rollback");
        check(GSON.toJson(disabled.report).contains("disabled-with-current-native-fault-history"),"fault preservation has an explicit report reason");
        feeder.setEnabled(true);feeder.setFeedCount(5);feeder.setEnabled(false);NativeConfigurationSnapshots.Restore consumed=NativeConfigurationSnapshots.prepareRestore(config,snapshot);consumed.apply();
        check(feeder.getFeedCount()==5&&!feeder.isEnabled(),"newer consumption and disable survive JSON-decoded rollback");
        check(GSON.toJson(consumed.report).contains("disabled-after-newer-consumption"),"consumption preservation has an explicit report reason");
        feeder.setFeedCount(41);double speed=machine.getSpeed();reject("RESTORE_MATERIAL_CONFLICT",()->NativeConfigurationSnapshots.prepareRestore(config,snapshot));check(feeder.getFeedCount()==41&&machine.getSpeed()==speed,"older capacity rejects without effects");
        feeder.setFeedCount(-1);reject("SNAPSHOT_INVALID",()->NativeConfigurationSnapshots.capture(config));reject("RESTORE_MATERIAL_CONFLICT",()->NativeConfigurationSnapshots.prepareRestore(config,snapshot));feeder.setFeedCount(5);
        passed.add("decoded restore preserves current native fault observations, consumed counts and disable floors, and rejects invalid/over-capacity material history");
    }
}
