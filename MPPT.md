# MPPT algorithm specific configuration
This software supports several different maximum power point tracking algorithms. They can be seclected and configured through the `--mppt-params` command line argument.
## basic algorithm
Alternates between periods of exploration and constant voltage dwelling with dwell period voltage determined by the previous exploration period's voltage at maximum power point.  
Usage: ` basic://[degrees]:[dwell]`  
__[degrees]__  
Default = 7   
Sets the upper (+__[degrees]__) and lower (-__[degrees]__) exploration limits for the exploration phasees of the algorithm. Larger numbers mean wider exploration.  
__[dwell]__  
Default = 10  
Sets the length of the dwell periods of the algorithm in seconds.

## gradient descent algorithm
Uses the difference in power between voltage measurement points to find and stay at peak power output.
Usage: `--mppt-params gradient_descent://[alpha]`
__[alpha]__
Default = 0.001
The "learning rate" should be between 1 and 0. Higher values mean the algorithm will respond more quickly to perturbations, but may oscillate. Lower values will mean slower response and less oscillations.

