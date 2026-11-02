/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.util.*;
import java.util.concurrent.CountDownLatch;
import org.openpnp.model.Configuration;

/** Initial launch only, with no fixture acceleration or default-configuration fallback. */
public final class NativeAdoptedSimulatorMain {
    public static void main(String[] args)throws Exception {
        Map<String,String> options=NativePortableConfigurationMain.options(args,Set.of("--adoption-dir","--token-file","--journal-dir","--sample-root","--port"));
        java.nio.file.Path tokenFile=NativePortableConfigurationMain.path(options,"--token-file"),samples=NativePortableConfigurationMain.path(options,"--sample-root");
        if(NativePortableConfiguration.read(tokenFile,4096).length<32)throw new IllegalArgumentException("Token file must contain at least32 bytes");if(!java.nio.file.Files.isDirectory(samples))throw new IllegalArgumentException("Native sample root absent");int port=Integer.parseInt(options.getOrDefault("--port","0"));if(port<0||port>65535)throw new IllegalArgumentException("Invalid loopback port");
        NativePortableLaunch launch=NativePortableLaunch.claim(NativePortableConfigurationMain.path(options,"--adoption-dir"),NativePortableConfigurationMain.path(options,"--journal-dir"));
        Bridge bridge=null;
        try {
            Configuration config=launch.initialize();
            bridge=new Bridge(config,tokenFile,launch.journalDirectory,samples,port,true,"adopted-simulator",null,launch);
            final Bridge live=bridge;Runtime.getRuntime().addShutdownHook(new Thread(()->{try{live.close();}catch(Exception ignored){}try{launch.close();}catch(Exception ignored){}}));
            bridge.start();launch.ready(bridge);System.out.println("OPENPNP_CODEX_READY port="+bridge.getPort()+" upstream="+Bridge.UPSTREAM+" mode=adopted-simulator");new CountDownLatch(1).await();
        }catch(Exception|Error failure){if(bridge!=null)try{bridge.close();}catch(Exception ignored){}try{launch.close();}catch(Exception ignored){}throw failure;}
    }
}
