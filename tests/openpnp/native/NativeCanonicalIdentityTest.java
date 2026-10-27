/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
/** Real native instance ownership and deferred part linkage; no physical effects. */
public final class NativeCanonicalIdentityTest {
 public static void main(String[] args)throws Exception {
  Configuration.initialize(Files.createTempDirectory("openpnp-canonical-identity-").toFile());Configuration c=Configuration.get();c.load();int assertions=0;
  try {
   for(boolean fresh:List.of(false,true)) {
    JsonObject input=NativeBoardLoadsTest.canonical(c.getPart("R0603-1K"));
    String partId=fresh?"CANONICAL-NEW-PART":"R0603-1K";
    if(fresh){input.getAsJsonArray("parts").get(0).getAsJsonObject().addProperty("id",partId);for(JsonElement b:input.getAsJsonArray("boards"))for(JsonElement p:b.getAsJsonObject().getAsJsonArray("placements"))p.getAsJsonObject().addProperty("partId",partId);}
    Job job=CanonicalJobImporter.load(c,input);List<BoardLocation> boards=job.getBoardLocations();Board a=boards.get(0).getBoard(),b=boards.get(1).getBoard();
    check(a!=b,"native Board instance objects must be distinct");assertions++;
    check(a.getDefinition()==b.getDefinition(),"native instances must retain a shared definition");assertions++;
    Placement pa=a.getPlacements().get(0),pb=b.getPlacements().get(0);
    check(pa!=pb&&pa.getDefinition()==pb.getDefinition(),"placement instances must be distinct with shared native definition");assertions++;
    check(pa.getPart()==c.getPart(partId)&&pb.getPart()==c.getPart(partId),"existing and deferred part links reach every copied instance");assertions++;
    pa.setEnabled(false);check(pb.isEnabled(),"an instance-only edit does not mutate its sibling");assertions++;
    Map<String,Object> inspected=NativePlacementEdits.inspect(c,job,0,200);check(((Number)inspected.get("total_records")).intValue()==4,"real inspector accepts separate native instances");assertions++;
   }
   check(!c.getMachine().isEnabled()&&!c.getMachine().isHomed(),"passive test must not enable/home");assertions++;
   System.out.println("OPENPNP_NATIVE_CANONICAL_IDENTITY_RESULT "+new Gson().toJson(Bridge.map("assertions",assertions,"native_feed_effects",0,"simulation_only",true)));
  }finally{c.getMachine().close();}System.exit(0);
 }
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
}
