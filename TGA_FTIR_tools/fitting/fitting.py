import numpy as np
import pandas as pd
import scipy as sp
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from ..plotting import get_label
from ..config import UNITS, PARAMS, SEP, DPI, BOUNDS, PATHS, COUPLING
from ..input_output.general import time
import os
import copy
import re


def gaussian(x,height,center,hwhm):
    "evaluate gaussian function with height, center and HWHM at x"
    return height*np.exp(-np.log(2)*np.power((x-center)/hwhm,2))

def multi_gauss(x,*args):
    "evaluate sum of multiple gaussian functions with height, center and HWHM at x"
    n=int(len(args)/3)
    heights=args[:n]
    centers=args[n:2*n]
    hwhms=args[2*n:len(args)]
    
    s=0
    for i in range(n):
        s=s+gaussian(x,heights[i],centers[i],hwhms[i])
    return s

def baseline_als(y, lam=1e6, p=0.01, niter=10): #https://stackoverflow.com/questions/29156532/python-baseline-correction-library
    L = len(y)
    D = sp.sparse.csc_matrix(np.diff(np.eye(L), 2))
    w = np.ones(L)
    for i in range(niter):
        W = sp.sparse.spdiags(w, 0, L, L)
        Z = W + lam * D.dot(D.transpose())
        z = sp.sparse.linalg.spsolve(Z, w*y)
        w = p * (y > z) + (1-p) * (y < z)
    return z

def fitting(TG_IR, presets, func=multi_gauss, y_axis='orig', plot=False, save=True, predef_tol=0.01, title = True):
    "deconvolve IR data of TG_IR with func and multiple presets"
    temp_presets=copy.deepcopy(presets)
    data=[]
    
    # extract links between groups and sort gases accordingly
    for key in temp_presets:
        data+=[temp_presets[key].loc[:,'link'].rename(key)]
    links=pd.concat(data,axis=1)
    gas_links=links.replace('0',np.nan).dropna(thresh=1).dropna(thresh=1,axis=1)
    if gas_links.dropna(axis=1).empty and not links.replace('0',np.nan).dropna(thresh=1).empty:
        print('You cannnot predefine fitting parameters for all supplied gases!')
        gas_links=pd.DataFrame()
        links=pd.DataFrame()
    gases=[key for key in temp_presets]
    for gas in gas_links.columns:
        gases.remove(gas)
        gases.append(gas)
    
    #thresholds for fit parameters
    ref_mass=TG_IR.info['reference_mass']
    
    #initializing output DataFrame
    peaks=pd.DataFrame(columns=['center','height','hwhm','area','mmol','mmol_per_mg'],index=[group+'_'+gas for gas in gases for group in temp_presets[gas].index]+[gas for gas in gases])
    sumsqerr=pd.DataFrame(index=[TG_IR.info['name']],columns=gases)

    #cycling through gases
    FTIR=TG_IR.ir.copy()
    for gas in gases:
        # correction of water-signal drift
        if gas=='H2O':
            FTIR[gas]-=baseline_als(FTIR[gas])
            
        # molar desorption
        tot_area=np.sum(TG_IR.ir[gas])
    
        if gas == 'H2O':
            # total area of water is calculated above dry-point, if this exists (therefore, try)
            try:
                minimum_temp = TG_IR.info['dry_temp']
            except:
                minimum_temp = TG_IR.ir[gas].min()
            tot_area=np.sum(TG_IR.ir[gas][TG_IR.ir['sample_temp']>minimum_temp])
        
        if 'linreg' in TG_IR.__dict__:
            tot_mol=(tot_area-TG_IR.linreg['intercept'][gas])/TG_IR.linreg['slope'][gas]
            peaks['mmol'][gas]=tot_mol
            peaks['mmol_per_mg'][gas]=tot_mol/TG_IR.info[ref_mass]
            
        peaks['area'][gas]=tot_area
        
        if y_axis=='rel':
            FTIR.update(FTIR[gas]/tot_area*tot_mol)
            
        for key in ['_0','_min','_max']:
            temp_presets[gas].loc[:,'height'+key]=temp_presets[gas].loc[:,'height'+key].multiply(max(TG_IR.ir[gas]))
        
        # predefining guesses and bounds
        if gas in gas_links:
            df=links.dropna(thresh=2).replace('0',np.nan).dropna(thresh=1)
            for group in df.index:
                other=links.loc[group,:].index[links.loc[group]=='0'].values[0]
                for letter in df.loc[group,gas]:
                    if letter=='c':
                        param='center'
                    if letter=='w':
                        param='hwhm'
                    if letter=='h':
                        param='height'
                    if param=='height':
                        preset=peaks.loc['{}_{}'.format(group,other),param]/TG_IR.linreg['slope'][other]*TG_IR.linreg['slope'][gas]
                    else:
                        preset=peaks.loc['{}_{}'.format(group,other),param]
                    temp_presets[gas].loc[group,'{}_0'.format(param)]=preset 
                    temp_presets[gas].loc[group,'{}_min'.format(param)]=preset*(1-predef_tol)
                    temp_presets[gas].loc[group,'{}_max'.format(param)]=preset*(1+predef_tol)
        
        # guesses 
        params_0=np.concatenate(([temp_presets[gas].loc[:,key+'_0'] for key in ['height', 'center', 'hwhm']])) 
        
        # ...and bounds
        params_min=np.concatenate(([temp_presets[gas].loc[:,key+'_min'] for key in ['height', 'center', 'hwhm']])) 
        params_max=np.concatenate(([temp_presets[gas].loc[:,key+'_max'] for key in ['height', 'center', 'hwhm']])) 
        
        # actual fitting
        x=FTIR['sample_temp']
        try:
            popt,pcov=sp.optimize.curve_fit(func,x,FTIR[gas],p0=params_0,bounds=(params_min,params_max))
        except:
            print('Failed to fit {} signal'.format(gas))
            break
        
        # return values
        num_curves=len(temp_presets[gas])
        for i in range(num_curves):
            group=temp_presets[gas].index[i]+'_'+gas
            peaks['height'][group]=popt[i]
            peaks['center'][group]=popt[i+num_curves]
            peaks['hwhm'][group]=popt[i+2*num_curves]
            if y_axis=='orig':
                peaks['area'][group]=np.sum(gaussian(x,popt[i],popt[i+num_curves],popt[i+2*num_curves]))
                #peaks['mmol'][group] = peaks['area'][group]/tot_area*tot_mol   # calculation based relative to total evolved gas
                peaks['mmol'][group] = (peaks['area'][group] - TG_IR.linreg['intercept'][gas]) / TG_IR.linreg['slope'][gas]   # calculation based on each Gauß fit
                peaks['mmol_per_mg'][group] = peaks['mmol'][group]/TG_IR.info[ref_mass]
                ir_values = 'area'
            elif y_axis=='rel':
                #peaks['mmol'][group]=peaks['area'][group]/tot_area*tot_mol   # calculation based relative to total evolved gas
                peaks['mmol'][group] = (peaks['area'][group] - TG_IR.linreg['intercept'][gas]) / TG_IR.linreg['slope'][gas]   # calculation based on each Gauß fit
                peaks['mmol_per_mg'][group]=peaks['mmol'][group]/TG_IR.info[ref_mass]
                ir_values = 'mmol_per_mg'
        
        # plotting
        profiles=pd.DataFrame()
        data=FTIR[gas]
        fit=multi_gauss(x,*popt)
        diff=data-fit
        sumsqerr[gas][TG_IR.info['name']]=np.sum(np.power(diff,2))
        profiles['sample_temp']=x
        profiles['data']=data
        profiles['fit']=fit
        profiles['diff']=diff
        
        # plotting
        if plot:
            # setup plot
            fig=plt.figure(constrained_layout=True)
            gs = fig.add_gridspec(8, 1)
            fitting = fig.add_subplot(gs[:-1, 0])
            if (title == True):
                fitting.set_title('{}, {} = {:.2f} ${}$'.format(TG_IR.info['alias'], TG_IR.info['reference_mass'], TG_IR.info[TG_IR.info['reference_mass']],UNITS['sample_mass']))
            error = fig.add_subplot(gs[-1,0],sharex=fitting)
            #fitting.xaxis.set_ticks(np.arange(0, 1000, 50))
            
            # plotting of fit
            fitting.plot(x,data,label='data',lw=2,zorder=num_curves+1)#,ls='',marker='x',markevery=2,c='cyan')
            fitting.plot(x,fit,label='fit',lw=2,zorder=num_curves+2)
        for i in range(0,num_curves):
            y=gaussian(x,popt[i],popt[i+num_curves],popt[i+2*num_curves])
            profiles[temp_presets[gas].index[i]]=y
            if plot:
                fitting.text(popt[num_curves+i],popt[i],temp_presets[gas].index[i],zorder=num_curves+3+i)
                fitting.plot(x,y,linestyle='dashed',zorder=i)
        if plot:
            fitting.legend()
            fitting.set_xlabel('{} {} ${}$'.format(PARAMS['sample_temp'], SEP, UNITS['sample_temp']))
            if y_axis=='orig':
                fitting.set_ylabel('{} {} ${}$'.format(get_label(gas), SEP, UNITS['ir']))
            elif y_axis=='rel':
                fitting.set_ylabel('{} {} ${}\,{}^{{-1}}\,{}^{{-1}}$'.format(get_label(gas), SEP, UNITS['molar_amount'], UNITS['sample_mass'], UNITS['time']))

            # mark center on x-axis
            fitting.scatter(popt[num_curves:2*num_curves],np.zeros(num_curves),marker=7,color='k',s=100,zorder=num_curves+3)

            # plotting of absolute difference
            abs_max=0.05*max(data)
            
            error.text(0,abs_max,'        SQERR: {:.2e} ({:.2f}%)'.format(sumsqerr[gas][TG_IR.info['name']],   #,'SQERR: '+'%.2E'% Decimal(sumsqerr[gas][TG_IR.info['name']]),va='bottom')
                                                                          100 * sumsqerr[gas][TG_IR.info['name']] / peaks[ir_values][gas] ))   # percentage SQERR
            error.plot(x,diff)
            error.hlines(0,min(x),max(x),ls='dashed')
            error.set_xlabel('{} {} ${}$'.format(PARAMS['sample_temp'], SEP, UNITS['sample_temp']))
            error.set_ylabel('error') # {} ${}$'.format(SEP, UNITS['ir']))
            error.set_ylim(-abs_max,abs_max)
            
            fitting.xaxis.set_minor_locator(ticker.AutoMinorLocator())  # switch on minor ticks on each axis
            fitting.yaxis.set_minor_locator(ticker.AutoMinorLocator())
            error.xaxis.set_minor_locator(ticker.AutoMinorLocator())
            
            plt.show()
            fig.savefig(TG_IR.info['name']+'_'+gas+'.png', bbox_inches='tight', dpi=DPI)
        
        # save results to excel
        if save:
            f_name=TG_IR.info['name']+'_'+y_axis+'.xlsx'
            try:
                with pd.ExcelWriter(f_name,engine='openpyxl', mode='a') as writer:
                    profiles.to_excel(writer,sheet_name=gas)
                    temp_presets[gas].to_excel(writer,sheet_name=gas+'_param')
            except:
                with pd.ExcelWriter(f_name,engine='openpyxl') as writer:
                    profiles.to_excel(writer,sheet_name=gas)
                    temp_presets[gas].to_excel(writer,sheet_name=gas+'_param')
                    
    # calculate summarized groups
    #groups=list(set([re.split('_| ',group)[0] for group in peaks.index if group not in gases]))
    groups=list(set([re.split('_',group)[0] for group in peaks.index if group not in gases]))
    for group in groups:
        group_set=peaks[['mmol','mmol_per_mg']].loc[peaks.index.map(lambda x: x.startswith(group))]
        if len(group_set)>1:
            peaks=peaks.append(pd.DataFrame(group_set.sum(axis=0).rename(group+'_sum')).T)
            peaks=peaks.append(pd.DataFrame(group_set.mean(axis=0).rename(group+'_mean')).T)
    
    if save:                
        with pd.ExcelWriter(f_name,engine='openpyxl', mode='a') as writer:
                peaks.astype(float).to_excel(writer,sheet_name='summary')
    return peaks.astype(float),sumsqerr

def fits(objs,reference,save=True,presets=None,**kwargs):
    "perform decovolution on multiple TG_IR objects"
    
    # load default presets
    if presets==None:
        presets=get_presets(PATHS['dir_home'], reference)
        
    # initializing of output DataFrames
    err=pd.DataFrame()
    names=['center','height','hwhm','area','mmol','mmol_per_mg']
    results=dict()
    for name in names:
        results[name]=pd.DataFrame()
    
    # make subdirectory to save data
    if save:
        sample_names = "".join(list(set([str(obj.info['name']) for obj in objs])))
        sample_names = "".join([x if (x.isalnum() or x in "._- ") else "" for x in str(sample_names)]) # to catch invalide sample names
        path = os.path.join(PATHS['dir_fitting'],time()+reference+'_'+'_'+sample_names).replace(os.sep,os.altsep)
        # check path length and if necessary shorten file name by list of samples, regarding expacted .png files to be saved to this directory
        longest_name_length = 0
        for obj in objs:
            obj_name_length = len(obj.info['name'])
            if (obj_name_length > longest_name_length):
                longest_name_length = obj_name_length
        if ( (len(path)+longest_name_length) > 258):
            sample_names = sample_names[:(len(sample_names)-((len(path)+longest_name_length)-(258 - 8)))]   # -8 for _gas and .png
            path = os.path.join(PATHS['dir_fitting'],time()+reference+'_'+'_'+sample_names).replace(os.sep,os.altsep)
        os.makedirs(path)
        os.chdir(path)
    
    sample_re='^.+(?=_\d{1,3})'
    num_re='(?<=_)\d{1,3}$'
    sample_remember = ''
    # cycling through samples
    for i, obj in enumerate(objs):
        # fitting of the sample and calculating the amount of functional groups
        name=obj.info['alias']
        sample=re.search(sample_re,name)
        num=re.search(num_re,name)
        if sample!=None:
            sample=sample.group()
        else:
            sample=name
        
        if sample != sample_remember: 
            j = 1
        else: 
            j += 1
        sample_remember = sample
        
        if num!=None:
            num=num.group()
        else:
            num = j

        peaks, sumsqerr = obj.fit(reference, presets=presets, **kwargs, save=False)

        #writing data to output DataFrames
        for key in results:
            results[key]=results[key].append(pd.concat({sample:pd.DataFrame(peaks[key].rename(num)).T}, names=['samples','run']))  
        err=err.append(pd.concat({sample:sumsqerr.rename({name:num})}, names=['samples','run']))
        
    # calculate statistical values
    dm = COUPLING.getfloat('mass_resolution')*1e-3
    for key in results:
        samples = results[key].index.levels[0]
        for sample in samples:
            
            if key=='mmol_per_mg':
                drop_cols = [col for col in results[key].columns if ('_sum' in col) or ('_mean' in col)]
                columns = results[key].columns.drop(drop_cols)
                group_gas = [column[column.rfind('_')+1:] for column in columns]

                lod = [objs[0].stats['x_LOD'][gas] for gas in group_gas]
                subset = results['mmol_per_mg'].loc[sample,columns]
                mmol = results['mmol'].loc[sample,columns]
                g = mmol / subset
                
                dmmolg_i = np.power(np.power(lod/mmol,2) + np.power(dm/g,2),0.5) * subset
                dmmol = np.power(np.sum(np.power(dmmolg_i,2)),0.5)
            
                stddev = pd.concat([pd.DataFrame(dmmol.rename('dev')).T,pd.DataFrame(results[key].loc[sample,drop_cols].std().rename('dev')).T],axis=1)

            else:
                stddev = pd.DataFrame(results[key].loc[sample].std().rename('stddev')).T
            mean = pd.DataFrame(results[key].loc[sample].mean().rename('mean')).T

            results[key] = results[key].append(pd.concat({sample:mean}, names=['samples','run']))
            results[key] = results[key].append(pd.concat({sample:stddev}, names=['samples','run']))
        
        #results[key].sort_index(inplace=True)   # sorting by rows would effect the order of mean and stddev or dev and mean, respectively.
        results[key].sort_index(axis=1,inplace=True)   # sorting by columns    
    
    # check results for LOD and LOQ and add columns 'rel_dev', limits
    gases=[key for key in presets]
    results['mmol'] = check_LODQ(results['mmol'], samples, gases, objs, mean = 'mean', dev = 'stddev', ref_mass = False)
    results['mmol_per_mg'] = check_LODQ(results['mmol_per_mg'], samples, gases, objs, mean = 'mean', dev = 'dev')
    
    # exporting data
    if save:
        print('Fitting finished! Plots and results are saved in \'{}\'.'.format(path))
        with pd.ExcelWriter('summary.xlsx') as writer:
            for key in results:
                results[key].dropna(axis=1,thresh=1).to_excel(writer,sheet_name=key)
            err.to_excel(writer,sheet_name='sum_squerr')
        os.chdir(PATHS['dir_home'])
    return results

def get_presets(path,reference):
    "load deconvolution presets from excel file"
    # load raw data from file
    presets=dict()
    try:
        references=pd.read_excel(os.path.join(path,'Fitting_parameter.xlsx'),index_col=0,header=None,sheet_name=None)
    except:
        print('The Fitting_parameter.xlsx file could not be loaded, please supply it in', PATHS['dir_home'])
    gases=list(set(references['center_0'].loc['gas']))
    
    # organizing data in dict, sorted by gases and filling in missing values with [fitting] parameters of settings.ini
    for gas in gases:
        index=[references['center_0'].loc['group',i] for i in references['center_0'].columns if (references['center_0'].loc['gas',i].upper()==gas)]
        data=pd.DataFrame(index=index)
        for key in references:
            data[key]=pd.DataFrame(references[key].loc[reference,:][references[key].loc['gas',:]==gas].T.values,index=index,columns=[key])#.dropna(axis=1)
        presets[gas]=data.dropna(axis=0,how='all')    
        
        params=['height_0', 
            'hwhm_0', 
            'center_min', 
            'hwhm_min',
            'height_min', 
            'center_max',
            'hwhm_max', 
            'height_max',
            'link']
        vals=[BOUNDS.getfloat('height_0'),
              BOUNDS.getfloat('hwhm_0'), 
              pd.Series(presets[gas].loc[:,'center_0']-BOUNDS.getfloat('tol_center')), 
              BOUNDS.getfloat('hwhm_min'),
              BOUNDS.getfloat('height_min'), 
              pd.Series(presets[gas].loc[:,'center_0']+BOUNDS.getfloat('tol_center')), 
              BOUNDS.getfloat('hwhm_max'), 
              BOUNDS.getfloat('height_max'),
              '0']
        infill=dict(zip(params,vals))
        presets[gas]=presets[gas].fillna(infill).dropna()
        if presets[gas].empty:
            del presets[gas]
    
    return presets


def check_LODQ(results_table, samples, gases, objs, mean = 'mean', dev = 'stddev', meandev = 'meanstddev', ref_mass = True):
    # check results for LOD and LOQ and add columns 'rel_stddev', 'rel_meanstddev', limits
    for sample in samples:
        LODQ_test = results_table.T
        
        # calculate the mean of objects reference masses to be able to check for limits
        # This makes the function limited to measurments with comparable samples masses
        if (ref_mass == True):   # results table in mmol_per_mg
            ref_mass_mean = 0
            for obj in objs:
                ref_mass_mean += obj.info[obj.info['reference_mass']]
            ref_mass_mean = ref_mass_mean / len(objs)
        else:
            ref_mass_mean = 1   # results table in mmol

        # add column 'rel_stddev'
        try:   # for fits() if only one sample is analyzed
            LODQ_test[(sample, ('rel_'+dev))] = LODQ_test.loc[:,(sample, dev)] / LODQ_test.loc[:,(sample, mean)]
        except:
            pass
        try:   # for robustness() only
            LODQ_test[(sample, ('rel_'+meandev))] = LODQ_test.loc[:,(sample, meandev)] / LODQ_test.loc[:,(sample, mean)]
        except:
            pass

        # add column 'limits' and check mean for LOQ and LOD
        LODQ_test[(sample, 'limits')] = ''
        for limit in ['LOQ', 'LOD']:
            for gas in gases:
                if (gas == 'CO'):
                    list_gas = np.array(list(set(filter(lambda x: gas in x, LODQ_test.index)) - set(filter(lambda x: 'CO2' in x, LODQ_test.index))))
                    list_gas = list_gas[list(((LODQ_test.loc[list_gas,(sample, mean)] * ref_mass_mean) < objs[0].stats.loc[gas,('x_' + limit)]).values)]
                    LODQ_test.loc[list_gas,(sample, 'limits')] = ('< ' + limit)                
                else:
                    list_gas = np.array(list(filter(lambda x: gas in x, LODQ_test.index)))
                    list_gas = list_gas[list(((LODQ_test.loc[list_gas,(sample, mean)] * ref_mass_mean) < objs[0].stats.loc[gas,('x_' + limit)]).values)]
                    LODQ_test.loc[list_gas,(sample,'limits')] = ('< ' + limit)
    
        results_table = LODQ_test.T
    
    # sort at sample level to keep new rows together with other rows of the samples
    #results_table.sortlevel(level = 'samples', ascending = samples, sort_remaining = False)
    
    return results_table
