# MPPT algorithm specific configuration
This software supports several different maximum power point tracking algorithms. They can be seclected and configured through the `--mppt-params` command line argument. The default MPPT is `--mppt-params basic://7:10`  
## basic algorithm
Alternates between periods of exploration and constant voltage dwelling with dwell period voltage determined by the previous exploration period's voltage at maximum power point.  
Usage: `--mppt-params basic://[degrees]:[dwell]`  
just `--mppt-params basic://` runs with the default values  
__[degrees]__  
Default = 7   
Sets the upper (+__[degrees]__) and lower (-__[degrees]__) exploration limits for the exploration phasees of the algorithm. Larger numbers mean wider exploration.  
__[dwell]__  
Default = 10  
Sets the length of the dwell periods of the algorithm in seconds.

## gradient descent algorithm
Uses the difference in power between voltage measurement points to find and stay at peak power output.  
Usage: `--mppt-params gradient_descent://[alpha]:[min_step]:[fade_in]`  
just `--mppt-params gradient_descent://` runs with the default values   
__[alpha]__  
Default = 10  
The "learning rate" should be > 0. Higher values mean the algorithm will respond more quickly to changes, but may oscillate or otherwise be unstable around the max power point. Lower values will mean slower response and less instability.  
__[min_step]__  
Default = 0.001  
The minimum voltage step size should be >= 0. Lower values mean the algorithm will be smoother with less oscillations around the max power point, but too low seems to cause the algorithm to get stuck and unable to respond to changes.  
__[fade_in]__  
Default = 10  
Number of seconds to use to ramp the learning rate from 0 to alpha at the start of the algorithm. If this is too small, the tracker might experience large jumps at the start.
