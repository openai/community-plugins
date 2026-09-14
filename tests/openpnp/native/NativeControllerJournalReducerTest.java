/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import org.openpnp.codex.prototype.tagged.NativeDiagnosticBridgeTest;
import static org.openpnp.codex.NativeControllerTestProcesses.*;

/** Generates an actual normal native journal, then applies the independent typed-reducer mutations. */
public final class NativeControllerJournalReducerTest {
    public static void main(String[] args)throws Exception{
        if(args.length!=1)throw new IllegalArgumentException("Expected pinned sample root");Path samples=Paths.get(args[0]).toRealPath(),root=Files.createTempDirectory("openpnp-controller-reducer-").toRealPath(),producer=root.resolve("producer");
        JsonObject process=run(NativeDiagnosticBridgeTest.class,producer,0,"normal",producer.toString(),samples.toString()),nativeResult=read(producer.resolve("observation.json"));require(nativeResult.get("passed").getAsBoolean(),"Actual normal fixture failed");
        Path journal=producer.resolve("journal/operations.jsonl");String before=sha(journal);JsonArray all=events(journal),controller=new JsonArray();JsonObject admitted=null,terminal=null;
        for(JsonElement raw:all){JsonObject event=raw.getAsJsonObject();String type=event.get("type").getAsString();if(type.startsWith("controller_diagnostic_"))controller.add(event);if(type.equals("operation")&&event.getAsJsonObject("payload").get("method").getAsString().equals("openpnp_run_controller_diagnostic")){if(admitted==null)admitted=event.getAsJsonObject("payload");terminal=event.getAsJsonObject("payload");}}
        require(admitted!=null&&terminal!=null&&terminal.get("state").getAsString().equals("succeeded"),"Actual successful native fixture required");Path fixture=root.resolve("fixture.json"),result=root.resolve("reducer.json");save(fixture,object("controller_events",controller,"admitted_operation",admitted,"terminal_operation",terminal));
        NativeControllerReducerReviewTest.main(new String[]{fixture.toString(),result.toString()});JsonObject reducer=read(result);require(reducer.get("passed").getAsBoolean()&&sha(journal).equals(before),"Reducer failed or modified source journal");
        JsonObject report=object("passed",true,"suite","NativeControllerJournalReducerTest","assertions",nativeResult.get("assertions").getAsInt()+reducer.get("checks").getAsInt(),"native_fixture_assertions",nativeResult.get("assertions").getAsInt(),"reducer_checks",reducer.get("checks").getAsInt(),"source_journal_sha256",before,"source_journal_unchanged",true,"native_fixture_process",process,"bridge_artifact_sha256",nativeResult.getAsJsonObject("capabilities").get("bridge_artifact_sha256"),"reducer_report",result.toString(),"physical_qualification",false);save(root.resolve("report.json"),report);System.out.println("OPENPNP_CONTROLLER_REDUCER_RESULT "+report);
    }
}
