/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import java.util.concurrent.ConcurrentHashMap;
import java.util.prefs.*;

/** Fresh GUI-session preferences. Never reads/writes OS preferences or the filesystem. */
public final class IsolatedPreferencesFactory implements PreferencesFactory {
    private final Preferences user=new MemoryNode(null,""),system=new MemoryNode(null,"");
    public Preferences userRoot(){return user;}
    public Preferences systemRoot(){return system;}
    public static final class MemoryNode extends AbstractPreferences {
        private final ConcurrentHashMap<String,String> values=new ConcurrentHashMap<>();
        private final ConcurrentHashMap<String,MemoryNode> children=new ConcurrentHashMap<>();
        MemoryNode(MemoryNode parent,String name){super(parent,name);}
        protected void putSpi(String key,String value){values.put(key,value);}
        protected String getSpi(String key){return values.get(key);}
        protected void removeSpi(String key){values.remove(key);}
        protected void removeNodeSpi(){values.clear();children.clear();if(parent()!=null)((MemoryNode)parent()).children.remove(name(),this);}
        protected String[] keysSpi(){return values.keySet().toArray(new String[0]);}
        protected String[] childrenNamesSpi(){return children.keySet().toArray(new String[0]);}
        protected AbstractPreferences childSpi(String name){return children.computeIfAbsent(name,key->new MemoryNode(this,key));}
        protected void syncSpi(){}
        protected void flushSpi(){}
    }
}
