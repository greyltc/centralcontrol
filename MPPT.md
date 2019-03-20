# Maimum power point tracking algorithm specific configuration

## basic algorithm
Alternates between periods of exploration and constant voltage dwelling with dwell period voltage determined by the previous exploration period's voltage at maximum power point.  
Usage: `--mppt-params basic://[degrees]:[dwell]`  
__[degrees]__  
Default = 7   
Sets the upper (+__[degrees]__) and lower (-__[degrees]__) exploration limits for the exploration phasees of the algorithm. Larger numbers mean wider exploration.  
__[dwell]__  
Default = 10  
Sets the length of the dwell periods of the algorithm in seconds.
