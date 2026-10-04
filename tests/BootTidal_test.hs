:set -fno-warn-orphans -Wno-type-defaults -XMultiParamTypeClasses -XOverloadedStrings
:set prompt ""
:set prompt-cont ""

import Sound.Tidal.Boot
import qualified System.IO as IO
import qualified System.Environment as Env

-- Tidal's threads print while GHCi does; unbuffered output interleaves
-- them character by character.
IO.hSetBuffering IO.stdout IO.LineBuffering

default (Rational, Integer, Double, Pattern String)

seleneCtrlPort <- maybe 6010 read <$> Env.lookupEnv "SELENE_CTRL_PORT" :: IO Int
tidalInst <- mkTidalWith [(superdirtTarget { oPort = 57999 }, [superdirtShape])] (defaultConfig {cCtrlPort = seleneCtrlPort})

instance Tidally where tidal = tidalInst
