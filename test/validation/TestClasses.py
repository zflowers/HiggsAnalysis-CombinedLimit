import os, os.path, re, json, stat
import ROOT
from math import *

class Test:
    def __init__(name):
        self._name = name
    def name(self):
        return self._name
    def scriptName(self,dir):
        return "%s/run_%s.sh" % (dir,self._name)
    def logName(self,dir):
        return "%s/%s.log" % (dir,self._name)
    def jsonName(self,dir):
        return "%s/%s.json" % (dir,self._name)
    def createScriptBase(self,dir):
        """Creates script file, writes preamble, then calls createScript to fill it"""
        print "  creating script", self.scriptName(dir)
        fname = self.scriptName(dir)
        output = open(fname, "w")
        self._createPreamble(output,dir)
        self.createScript(dir,output)
        output.close()
        os.chmod(fname,stat.S_IRWXU)
    def createScript(self,dir,file):
        """Print out whatever commands have to be printed in the script after it's created"""
        raise RuntimeError, "Not implemented"
    def readOutputBase(self,dir):
        """Calls readOutput to read the output, saves it as JSON file"""
        output = self.readOutput(dir)
        if output != None:
            fout = open(self.jsonName(dir), "w");
            fout.write(json.dumps({self._name : output}, sort_keys=True, indent=4))
            fout.close()
            print "  wrote individual output to",self.jsonName(dir)
        return output
    def readOutput(self,dir):
        """Read output, and return output as python object"""
        raise RuntimeError, "Not implemented"
    def _createPreamble(self,file,dir):
        E=os.environ;
        file.write("#!/bin/bash\n")
        file.write("cd %s/%s;\n" % (os.getcwd(), dir));
        file.write('if [[ "$HOSTNAME" != "%s" ]]; then\n'
                   "     export SCRAM_ARCH=%s;          \n"
                   "     eval $(scram runtime -sh);     \n"
                   "fi\n" % (E['HOSTNAME'], E['SCRAM_ARCH']));
        file.write(": > %s.log\n" % self._name);
        

def _readRootFile(fname):
        if os.access(fname, os.R_OK) == False: return { 'status':'aborted', 'comment':'rootfile does not exist' }
        if os.stat(fname).st_size < 1000:   return { 'status':'aborted', 'comment':'rootfile is smaller than 1kb' }
        f = ROOT.TFile.Open(fname)
        if not f: return { 'status':'aborted', 'comment':'rootfile is not readable by ROOT'}
        t = f.Get("limit")
        if not t: 
            f.Close()
            return { 'status':'aborted', 'comment':'rootfile does not contain the tree' }
        if t.GetEntries() == 0:
            return { 'status':'aborted', 'comment':'statistical method failed' }
        result = {'status':'done', 'comment':''}
        for c in [ "limit", "limitErr", "t_real" ]:  result[c] = t.GetMaximum(c)
        f.Close()
        if abs(result["limit"]) > 1e6: return { 'status':'aborted', 'comment':'rootfile %s contains bogus data' % fname}
        return result 


class SingleDatacardTest(Test):
    def __init__(self,name,datacard,method,options,mass=120):
        self._name = name+"_"+method
        self._datacard = datacard
        self._method   = method
        self._options  = options
        self._mass     = mass
    def createScript(self,dir,file):
        datacard_full = os.environ['CMSSW_BASE']+"/src/HiggsAnalysis/CombinedLimit/data/benchmarks/%s" % self._datacard
        if os.access(datacard_full, os.R_OK) == False: 
            raise RuntimeError, "Datacard HiggsAnalysis/CombinedLimit/data/benchmarks/%s is not accessible" % self._datacard
        file.write("combine %s -n %s -M %s %s -m %s >> %s.log 2>&1\n" % (datacard_full, self._name, self._method, self._options, self._mass, self._name));
    def readOutput(self,dir):
        out = _readRootFile("%s/higgsCombine%s.%s.mH%s.root" % (dir, self._name, self._method, self._mass))
        if out['status'] != 'done': return out
        return {'status':'done', 'comment':'', 'results':{self._datacard:out}}

class MultiDatacardTest(Test):
    def __init__(self,name,datacards,method,options):
        self._name = name + "_" + method
        self._datacards = [ (dc,i*10+100) for i,dc in enumerate(datacards) ]
        self._method   = method
        self._options  = options
    def createScript(self,dir,file):
        for dc, mass in self._datacards:
            datacard_full = os.environ['CMSSW_BASE']+"/src/HiggsAnalysis/CombinedLimit/data/benchmarks/%s" % dc 
            if os.access(datacard_full, os.R_OK) == False: 
                raise RuntimeError, "Datacard HiggsAnalysis/CombinedLimit/data/benchmarks/%s is not accessible" % dc 
            file.write("echo    %s -n %s -M %s %s -m %s >> %s.log     \n" % (datacard_full, self._name, self._method, self._options, mass, self._name));
            file.write("combine %s -n %s -M %s %s -m %s >> %s.log 2>&1\n" % (datacard_full, self._name, self._method, self._options, mass, self._name));
    def readOutput(self,dir):
        ret = { 'results':{}}
        for dc, mass in self._datacards:
            thisres = _readRootFile("%s/higgsCombine%s.%s.mH%s.root" % (dir, self._name, self._method, mass))
            ret['results'][os.path.basename(dc)] = thisres
        _summarize(ret)
        return ret

def _summarize(report):
    aborted = sum([ret['status'] == 'aborted' for ret in report['results'].values()])
    done    = sum([ret['status'] == 'done'    for ret in report['results'].values()])
    if aborted == 0 and done > 0:
        report['status'] = 'done'; 
        report['comment'] = 'all %d jobs done' % done; 
    elif done == 0 and aborted > 0:
        report['status'] = 'aborted'; 
        report['comment'] = 'all %d jobs aborted' % aborted; 
    else:
        report['status'] = 'partial'
        report['comment'] = '%d jobs done, %d jobs aborted' % (done, aborted)

def datacardGlob(pattern):
    from glob import glob
    base = os.environ['CMSSW_BASE']+"/src/HiggsAnalysis/CombinedLimit/data/benchmarks/"
    ret = [ f.replace(base, "") for f in glob(base+pattern) ]
    if ret == []: raise RuntimeError, "Pattern '%s' does not expand to any datacard in data/benchmarks." % pattern
    return ret 

class MultiOptionTest(Test):
    def __init__(self,name,datacard,method,commonOptions,optionsMap):
        self._name = name + "_" + method
        self._datacard = datacard 
        self._method   = method
        self._options  = [ (on,commonOptions+" "+ov,i*10+100) for i,(on,ov) in enumerate(optionsMap.items()) ]
    def createScript(self,dir,file):
        datacard_full = os.environ['CMSSW_BASE']+"/src/HiggsAnalysis/CombinedLimit/data/benchmarks/%s" % self._datacard
        if os.access(datacard_full, os.R_OK) == False: 
            raise RuntimeError, "Datacard HiggsAnalysis/CombinedLimit/data/benchmarks/%s is not accessible" % self._datacard
        for on, ov, mass in self._options:
            file.write("echo    %s -n %s -M %s %s -m %s >> %s.log     \n" % (datacard_full, self._name, self._method, ov, mass, self._name));
            file.write("combine %s -n %s -M %s %s -m %s >> %s.log 2>&1\n" % (datacard_full, self._name, self._method, ov, mass, self._name));
    def readOutput(self,dir):
        ret = { 'results':{}}
        for on, ov, mass in self._options:
            thisres = _readRootFile("%s/higgsCombine%s.%s.mH%s.root" % (dir, self._name, self._method, mass))
            ret['results'][on] = thisres
        _summarize(ret)
        return ret

