/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.nio.file.*;
import java.util.*;

/** Dedicated validation JVM. Receipt output grants only fresh simulator adoption. */
public final class NativePortableConfigurationMain {
    public static void main(String[] args)throws Exception {
        Map<String,String> options=options(args,Set.of("--bundle","--sha256","--destination"));
        NativePortableConfiguration.Prepared prepared=NativePortableConfiguration.prepare(path(options,"--bundle"),required(options,"--sha256"),path(options,"--destination"));
        Map<String,Object> result=NativePortableConfiguration.validateAndPublish(prepared);
        result.put("adoption_directory",prepared.destination.toString());result.put("archive_sha256",required(options,"--sha256"));result.put("first_launch_only",true);
        System.out.println("OPENPNP_CODEX_ADOPTED "+NativePortableConfiguration.JSON.toJson(result));
    }
    static Map<String,String> options(String[] args,Set<String> allowed){Map<String,String> options=new HashMap<>();for(int i=0;i<args.length;i+=2){if(i+1>=args.length||!allowed.contains(args[i])||options.put(args[i],args[i+1])!=null)throw new IllegalArgumentException("Expected distinct known --option value pairs");}return options;}
    static String required(Map<String,String> options,String key){String value=options.get(key);if(value==null)throw new IllegalArgumentException("Missing "+key);return value;}
    static Path path(Map<String,String> options,String key){Path value=Paths.get(required(options,key));if(!value.isAbsolute())throw new IllegalArgumentException(key+" must be absolute");return value.normalize();}
}
