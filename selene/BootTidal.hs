:set -fno-warn-orphans -Wno-type-defaults -XMultiParamTypeClasses -XOverloadedStrings
:set prompt ""
:set prompt-cont ""

import Sound.Tidal.Boot
import qualified System.IO as IO

-- Tidal's threads print while GHCi does; unbuffered output interleaves
-- them character by character.
IO.hSetBuffering IO.stdout IO.LineBuffering

default (Rational, Integer, Double, Pattern String)

tidalInst <- mkTidal

instance Tidally where tidal = tidalInst
