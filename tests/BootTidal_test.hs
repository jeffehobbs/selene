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
seleneTapPort <- maybe 0 read <$> Env.lookupEnv "SELENE_TAP_PORT" :: IO Int
seleneTap = [(superdirtTarget {oName = "selene", oPort = seleneTapPort, oBusPort = Nothing, oHandshake = False}, [superdirtShape]) | seleneTapPort > 0]
tidalInst <- mkTidalWith ((superdirtTarget { oPort = 57999 }, [superdirtShape]) : seleneTap) (defaultConfig {cCtrlPort = seleneCtrlPort})

instance Tidally where tidal = tidalInst
