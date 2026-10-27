/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.Configuration;
import org.w3c.dom.Document;
import static org.openpnp.codex.NativePortableConfiguration.*;

/** Version-specific dispatch; legacy flat-board entrypoints remain closed to panels. */
final class NativePortableLibraries {
    static boolean panels(JsonObject manifest){return manifest.has("version")&&"3".equals(manifest.get("version").toString());}
    static final class Captured {
        final NativePortableBoardLibrary.Captured boards;
        final NativePortablePanelLibrary.Captured panels;
        final Map<String,byte[]> entries=new TreeMap<>();
        final JsonObject libraryManifest;
        Captured(Configuration c)throws Exception {
            boolean hasPanels=!c.getPanels().isEmpty();boards=hasPanels?NativePortableBoardLibrary.captureForPanels(c):NativePortableBoardLibrary.capture(c);
            panels=hasPanels?NativePortablePanelLibrary.capture(c,boards):null;entries.putAll(boards.entries);if(panels!=null)entries.putAll(panels.entries);
            Map<String,Object> m=new TreeMap<>();m.put("version",version());addManifest(m);libraryManifest=JSON.toJsonTree(m).getAsJsonObject();
        }
        int version(){return panels!=null?3:boards.records.isEmpty()?1:2;}
        String scope(){return panels!=null?"saved clean flat board and direct-board-child panel definitions; jobs/board-load history excluded":boards.records.isEmpty()?"empty boards/panels libraries; job documents and board-load history excluded":"saved clean flat board definitions; panels/jobs/board-load history excluded";}
        void addManifest(Map<String,Object> m){if(panels!=null||!boards.records.isEmpty())m.put("board_library",boards.manifest());if(panels!=null)m.put("panel_library",panels.manifest());}
        void registry(Document doc,String name)throws Exception {if(name.equals("boards.xml"))NativePortableBoardLibrary.captureRegistry(doc,boards);if(panels!=null&&name.equals("panels.xml"))NativePortablePanelLibrary.registry(doc,libraryManifest,panels.sourcePaths,null);}
        void verifyCurrent(Configuration c)throws Exception {if(panels!=null)panels.verifyCurrent(c);}
    }
    static Captured capture(Configuration c)throws Exception {return new Captured(c);}
    static void validateArchive(Map<String,byte[]> entries,JsonObject m)throws Exception {if(panels(m))NativePortablePanelLibrary.validateArchive(entries,m);else {if(m.has("panel_library"))throw new Bridge.Fault("PORTABLE_PANEL_LIBRARY","Legacy archive cannot declare a panel library");NativePortableBoardLibrary.validateArchive(entries,m);}}
    static void stageRegistry(Document doc,String name,JsonObject m,Path stage)throws Exception {
        if(!panels(m)){if(name.equals("boards.xml"))NativePortableBoardLibrary.stageRegistry(doc,m,stage);return;}
        if(name.equals("boards.xml"))NativePortableBoardLibrary.rewriteRegistry(doc,null,NativePortableBoardLibrary.panelBoardRecords(m),true,stage);
        if(name.equals("panels.xml"))NativePortablePanelLibrary.registry(doc,m,null,stage);
    }
    static void stageAssets(Map<String,byte[]> entries,JsonObject m,Path stage)throws Exception {if(panels(m))NativePortablePanelLibrary.stagePanels(entries,m,stage);}
    static void normalizeRegistry(Document doc,String name,JsonObject m,Path stage)throws Exception {
        if(!panels(m)){if(name.equals("boards.xml"))NativePortableBoardLibrary.normalizeRegistry(doc,m,stage);return;}
        if(name.equals("boards.xml")){List<JsonObject> rows=NativePortableBoardLibrary.panelBoardRecords(m);List<String> paths=new ArrayList<>();for(JsonObject row:rows)paths.add(stage.resolve(string(row,"entry")).toString());NativePortableBoardLibrary.rewriteRegistry(doc,paths,rows,false,null);}
        if(name.equals("panels.xml"))NativePortablePanelLibrary.normalizeRegistry(doc,m,stage);
    }
    static Map<String,Object> verifyLoaded(Configuration c,JsonObject m,Path stage)throws Exception {if(!panels(m))return NativePortableBoardLibrary.verifyLoaded(c,m,stage);Map<String,Object> boards=NativePortableBoardLibrary.verifyPanelBoards(c,m,stage);NativePortablePanelLibrary.verifyLoaded(c,m,stage);return boards;}
    static void verifyFirstLaunch(Configuration c,Path config)throws Exception {Path stage=config.getParent();verifyLoaded(c,object(read(stage.resolve("manifest.json"),256*1024)),stage);}
    static void collectActivated(Map<String,byte[]> entries,JsonObject m,Path stage)throws Exception {
        if(!panels(m)){NativePortableBoardLibrary.collectActivated(entries,m,stage);return;}
        for(JsonObject row:NativePortableBoardLibrary.panelBoardRecords(m)){String entry=string(row,"entry");entries.put(entry,read(stage.resolve(entry),MAX_FILE));}
        Document boardRegistry=xml(entries.get("config/boards.xml"));normalizeRegistry(boardRegistry,"boards.xml",m,stage);entries.put("config/boards.xml",encode(boardRegistry));NativePortablePanelLibrary.collect(entries,m,stage);validateArchive(entries,m);
    }
}
