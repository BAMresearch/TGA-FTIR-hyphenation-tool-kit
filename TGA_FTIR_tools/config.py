# loading settings from settings.ini

import configparser
import os
file='settings.ini'
config = configparser.ConfigParser()
config.read(file)

PATHS=config['paths']

if PATHS['dir_home']=='':
    cwd=os.getcwd().replace(os.sep,os.altsep)
    print('No dir_home path was supplied in {}. dir_home was set to \'{}\''.format(file,cwd))
    config['paths']['dir_home']=cwd
if PATHS['dir_data']=='' or os.path.exists(PATHS['dir_data'])==False:
    print('\nNo valid dir_data path was supplied in \'{}\'.'.format(file))
    config['paths']['dir_data']=input('Supply directory of Data:').replace(os.sep,os.altsep)
    if os.path.exists(config['paths']['dir_data'])==False:
        print('\n!!! Supplied directory does not exist. Revise path in \'settings.ini\' prior to continue. !!!\n')

with open(file, 'w') as configfile:
    config.write(configfile)

UNITS=config['units']
keys=['sample_mass','time','sample_temp','molar_amount','heat_flow','dtg']
units=['mg','min','°C','mmol','mW','mg\,min^{{-1}}']
for key, val in zip(keys,units):
    UNITS[key]=val
    
SEP=UNITS['sep']

PARAMS=config['parameters']

MOLAR_MASS=config['molar_mass']

PLOTTING=config['plotting']

DPI=PLOTTING.getint('dpi')

LABELS=config['labels']

COUPLING=config['coupling']

SAVGOL=config['savgol']

BOUNDS=config['fitting']

IR_NOISE=config['ir_noise']


