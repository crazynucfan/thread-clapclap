#pragma once
#include <cstdint>

// Times are monotonically increasing 8kHz sound onsets, not inference times.
class CnnGesture {
 public:
  explicit CnnGesture(float threshold):threshold_(threshold) {}
  void reset() {cluster_=hasLast_=false;pending_=0;actions_=0;}
  void candidate(uint64_t onset,float score,bool top,double strength) {
    if (!top || score<threshold_) return;
    if (!cluster_ || onset-anchor_>=960) {
      flushCluster();cluster_=true;anchor_=best_=onset;score_=score;strength_=strength;
    } else if (strength>strength_ || (strength==strength_ && score>score_)) {
      best_=onset;score_=score;strength_=strength;
    }
  }
  bool advance(uint64_t watermark) {
    if (cluster_ && watermark>=anchor_+960) flushCluster();
    // An unresolved cluster may still contain a third clap before timeout.
    if (cluster_ && watermark>=anchor_) watermark=anchor_?anchor_-1:0;
    if (hasLast_ && watermark>=last_+4800) finish();
    if (actions_) {--actions_;return true;}return false;
  }
  unsigned pending() const {return pending_;}
 private:
  void finish() {if(pending_==2)++actions_;pending_=0;hasLast_=false;}
  void flushCluster() {
    if (!cluster_) return;
    if (hasLast_ && best_-last_>=4800) finish();
    if (!hasLast_ || best_-last_>=1280) {
      if (pending_<3)++pending_;
      last_=best_;hasLast_=true;
    }
    cluster_=false;
  }
  float threshold_,score_=0;
  double strength_=0;
  uint64_t anchor_=0,best_=0,last_=0;
  unsigned pending_=0,actions_=0;
  bool cluster_=false,hasLast_=false;
};
